#!/usr/bin/env python3
"""Branch surgery planner for PR branch topology governance.

Analyzes local and remote Git/PR state to detect branch topology issues
(shared head SHA, stacked PRs, etc.) and generates a dry-run plan with
concrete git commands (branch creation, cherry-pick, force-with-lease push)
and a post-push verification checklist.

This script never executes any Git mutation or push command.  It only
produces a Markdown report that Codex or the user must review and
explicitly execute.

Every report must include: affected PRs/head refs, verification commands,
risk assessment, and rollback plan.

Usage:
    python3 scripts/branch_surgery.py \\
        --output .deepseek-forge/branch_surgery.md

    python3 scripts/branch_surgery.py \\
        --pr-list '[{"number":1,"headRefName":"feat/a",...}, ...]'

    ``--output`` is optional.  The default is
    ``{artifact_dir}/branch_surgery.md``, where ``artifact_dir`` is
    resolved via :func:`forge_config.get_artifact_dir`.

Uses only stdlib — no external dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from forge_config import get_artifact_dir


# ---------------------------------------------------------------------------
# Input sanitisation
# ---------------------------------------------------------------------------

# Conservative shell-safe subset of git ref names.
# This is intentionally narrower than git-check-ref-format: characters
# like +, @, =, ~, ^, :, ?, *, [, \, and control chars are rejected.
# Ref names that fail this check cause a safe failure (ValueError).
_SAFE_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*$")
_SAFE_SHA_RE = re.compile(r"^[0-9a-fA-F]{3,64}$")
_SAFE_REMOTE_RE = re.compile(r"^(origin|[a-zA-Z0-9][a-zA-Z0-9._-]*|git@[a-zA-Z0-9._/-]+)$")
_SAFE_REMOTE_URL_RE = re.compile(r"^git@[^\s;|&$`\\]+$")


def _safe_ref(name: str, label: str = "ref") -> str:
    """Validate *name* as a conservative shell-safe git ref subset.

    Only ``[a-zA-Z0-9][a-zA-Z0-9._/-]*`` is accepted.  This is stricter
    than ``git check-ref-format`` and intentionally rejects refs containing
    ``+``, ``@``, ``^``, ``~``, ``:`` and other shell-significant characters.

    Returns the shell-quoted form.  Raises :class:`ValueError` on rejection.
    """
    if not name or not _SAFE_REF_RE.match(name):
        raise ValueError(f"Unsafe {label}: {name!r}")
    return shlex.quote(name)


def _safe_sha(sha: str, label: str = "SHA") -> str:
    """Validate *sha* as a plausible git object SHA and shell-quote it."""
    if not _SAFE_SHA_RE.match(sha):
        raise ValueError(f"Invalid {label}: {sha!r}")
    return shlex.quote(sha)


def _safe_remote(remote: str) -> str:
    """Validate *remote* (name or SSH URL) and shell-quote it."""
    if not _SAFE_REMOTE_RE.match(remote) and not _SAFE_REMOTE_URL_RE.match(remote):
        raise ValueError(f"Unsafe remote: {remote!r}")
    return shlex.quote(remote)


def _safe_int(value, label: str = "value") -> str:
    """Validate *value* is an integer and shell-quote it."""
    try:
        return shlex.quote(str(int(value)))
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {label}: {value!r}")


def _q(s: str) -> str:
    """Shorthand for :func:`shlex.quote`."""
    return shlex.quote(s)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def run_git(args: list[str], cwd: Path | None = None) -> str:
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=30,
        )
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _try_gh(args: list[str]) -> str:
    """Run a gh CLI command, return stdout or empty string."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


# ---------------------------------------------------------------------------
# PR data collection
# ---------------------------------------------------------------------------

# JSON fields requested by gh pr list for topology analysis.
_PR_LIST_FIELDS = (
    "number,title,headRefName,baseRefName,headRefOid,baseRefOid,state,url,"
    "headRepository,headRepositoryOwner"
)


def fetch_pr_list() -> list[dict]:
    """Fetch PR list from GitHub via ``gh pr list --json ...``.

    Returns a list of PR dicts including headRepositoryOwner for fork detection.
    """
    raw = _try_gh([
        "pr", "list",
        "--json", _PR_LIST_FIELDS,
        "--state", "open",
        "--limit", "50",
    ])
    if not raw.strip():
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"Warning: could not parse gh pr list output", file=sys.stderr)
        return []


def fetch_pr_commits(pr_number: int) -> list[dict]:
    """Fetch the commit list for a specific PR via ``gh pr view --json commits``.

    Returns a list of commit dicts with keys: oid, messageHeadline.
    """
    raw = _try_gh([
        "pr", "view", str(pr_number),
        "--json", "commits",
    ])
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
        return data.get("commits", [])
    except json.JSONDecodeError:
        print(f"Warning: could not parse gh pr view output for PR #{pr_number}", file=sys.stderr)
        return []


def fetch_pr_files(pr_number: int) -> list[str]:
    """Fetch the changed file paths for a PR via ``gh pr view --json files``.

    Returns a list of file path strings.
    """
    raw = _try_gh([
        "pr", "view", str(pr_number),
        "--json", "files",
    ])
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
        files = data.get("files", [])
        return [f["path"] for f in files if isinstance(f, dict) and f.get("path")]
    except (json.JSONDecodeError, KeyError):
        print(f"Warning: could not parse gh pr view files for PR #{pr_number}", file=sys.stderr)
        return []


def _commits_touching_files(base: str, head: str, files: list[str], repo_root: Path) -> list[str]:
    """Return commit SHAs from ``base..head`` that touch any of *files*.

    Uses ``git log --oneline --format=%H <range> -- <paths>``.
    Falls back to the full commit list if file-based filtering yields nothing.
    """
    if not files:
        return []

    args = ["log", "--oneline", "--format=%H", f"{base}..{head}", "--"]
    args.extend(files[:50])

    out = run_git(args, repo_root).strip()
    if out:
        return out.splitlines()

    all_out = run_git(
        ["log", "--oneline", "--format=%H", f"{base}..{head}"], repo_root
    ).strip()
    if all_out:
        return all_out.splitlines()

    return []


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def find_shared_heads(prs: list[dict]) -> dict[str, list[dict]]:
    """Group PRs by head SHA.

    Returns a dict mapping head SHA -> list of PR dicts that share it.
    Only keys with >1 PR are retained (shared heads).
    """
    groups: dict[str, list[dict]] = {}
    for pr in prs:
        head_sha = pr.get("headRefOid", "")
        if head_sha:
            groups.setdefault(head_sha, []).append(pr)
    return {sha: prs for sha, prs in groups.items() if len(prs) > 1}


def get_branch_info(repo_root: Path) -> dict:
    """Collect basic branch topology information."""
    current_branch = run_git(["branch", "--show-current"], repo_root).strip()
    remotes = run_git(["remote", "-v"], repo_root).strip()
    branches = run_git(["branch", "-a"], repo_root).strip()
    log_graph = run_git(
        ["log", "--oneline", "--graph", "--all", "-30"], repo_root
    ).strip()
    merge_base = ""
    if current_branch:
        for base in ["origin/main", "origin/master", "main", "master"]:
            mb = run_git(["merge-base", current_branch, base], repo_root).strip()
            if mb:
                merge_base = f"{current_branch}..{base}: {mb}"
                break

    return {
        "current_branch": current_branch,
        "remotes": remotes,
        "branches": branches,
        "commit_graph": log_graph,
        "merge_base": merge_base,
    }


def _resolve_push_remote(pr: dict, repo_root: Path) -> str:
    """Determine the correct remote for pushing to a PR's head branch.

    For fork PRs: searches local remotes for one whose URL contains the fork
    owner and returns that remote **name**.  If no matching remote is found,
    returns the fork's SSH URL for direct ``git push <url>`` usage.

    For same-repo PRs, returns ``origin``.
    """
    head_repo = pr.get("headRepository", {}) or {}
    head_owner = pr.get("headRepositoryOwner", {}) or {}
    head_owner_login = head_owner.get("login", "") if isinstance(head_owner, dict) else ""

    if head_owner_login and head_repo:
        remotes_text = run_git(["remote", "-v"], repo_root)
        # Search remotes for one whose URL contains github.com[:/]<owner>/
        owner_pattern = rf'github\.com[:/]{re.escape(head_owner_login)}/'
        match = re.search(owner_pattern, remotes_text)
        if match:
            # Owner IS in the remotes — find which remote name it belongs to.
            # remotes_text lines are like: "origin\tgit@github.com:owner/repo.git (fetch)"
            for line in remotes_text.strip().splitlines():
                if re.search(owner_pattern, line):
                    remote_name = line.split()[0] if line.split() else ""
                    if remote_name:
                        return remote_name
            # Shouldn't reach here, but safe fallback.
            return "origin"

        # Owner is NOT in any remote — this is a fork without a configured remote.
        ssh_url = head_repo.get("sshUrl", "") if isinstance(head_repo, dict) else ""
        if ssh_url:
            return ssh_url
        return head_owner_login

    return "origin"


def _is_fork_pr(pr: dict, repo_root: Path) -> bool:
    """Return True if the PR comes from a fork (head owner differs from origin's owner)."""
    push_target = _resolve_push_remote(pr, repo_root)
    # Fork if the push target is a URL (not a remote name) or a non-origin remote name.
    if push_target.startswith("git@"):
        return True
    return push_target != "origin"


def get_commit_info(ref: str, base: str, repo_root: Path) -> dict:
    """Get commit list and changed files between base and ref.

    *ref* may be a branch name, a remote tracking ref, or a commit SHA.
    """
    commits = run_git(
        ["log", "--oneline", f"{base}..{ref}"], repo_root
    ).strip()
    diff_stat = run_git(
        ["diff", "--stat", f"{base}..{ref}"], repo_root
    ).strip()
    changed_files = run_git(
        ["diff", "--name-only", f"{base}..{ref}"], repo_root
    ).strip()

    return {
        "commits": commits,
        "diff_stat": diff_stat,
        "changed_files": changed_files.splitlines() if changed_files else [],
    }


# ---------------------------------------------------------------------------
# Split plan generation
# ---------------------------------------------------------------------------


def generate_split_plan(
    shared_groups: dict[str, list[dict]],
    repo_root: Path,
) -> list[dict]:
    """For each shared-head group, generate a concrete split plan.

    Each element in the returned list describes one PR, including:

    - pr_number, title, head_ref, base_ref
    - Whether this PR is from a fork
    - suggested new branch name
    - **per-PR commit list** fetched from GitHub (``gh pr view --json commits``)
    - **Branch creation commands**::
        ``git checkout -b <new-branch> origin/<base_ref>``
    - **Cherry-pick commands** for only this PR's commits
    - Per-PR diff verification (only this PR's files)
    - Safe force-with-lease push command targeting the correct remote

    When ``gh`` is unavailable, falls back to a warning that manual
    cherry-pick range identification is needed.
    """
    plans: list[dict] = []

    for head_sha, prs in shared_groups.items():
        is_shared = len(prs) > 1
        # When PRs share BOTH base and head, the diff range is identical —
        # file-based commit isolation cannot distinguish them.
        same_base = (
            is_shared
            and len(set(p.get("baseRefName", "main") for p in prs)) == 1
        )

        for pr in prs:
            pr_num = pr.get("number", "?")
            head_ref = pr.get("headRefName", "unknown")
            base_ref = pr.get("baseRefName", "main")
            base_sha = pr.get("baseRefOid", "")

            suggested_branch = f"{head_ref}-pr-{pr_num}"
            is_fork = _is_fork_pr(pr, repo_root)
            push_remote = _resolve_push_remote(pr, repo_root)
            requires_manual_review = is_shared and same_base

            # Pre-compute shell-safe variants — every command token below
            # MUST use these, never the raw variables.
            safe_head_ref = _safe_ref(head_ref)
            safe_base_ref = _safe_ref(base_ref)
            safe_suggested_branch = _safe_ref(suggested_branch)
            safe_push_remote = _safe_remote(push_remote)

            branch_create_cmds: list[str] = []
            cherry_pick_cmds: list[str] = []
            per_pr_commit_lines: list[str] = []
            fetch_cmds: list[str] = []

            # --- Ensure commits are available locally ---
            if is_fork:
                fetch_cmds.append(
                    f"git fetch {safe_push_remote} refs/heads/{safe_head_ref}"
                )
            else:
                fetch_cmds.append(
                    f"git fetch origin refs/heads/{safe_head_ref}"
                )

            # --- Per-PR commit isolation ---
            if is_shared and same_base:
                # PRs share both base AND head — diff range is identical.
                # File-based isolation can't help; manual division is required.
                pr_commits = fetch_pr_commits(pr_num)
                branch_create_cmds.append(
                    f"# WARNING: PR #{pr_num} shares base ({base_ref}) AND head ({head_sha[:8]})"
                )
                branch_create_cmds.append(
                    f"# with other PRs. The commit list below may overlap — MANUAL REVIEW required."
                )
                branch_create_cmds.append(
                    f"git checkout -b {safe_suggested_branch} origin/{safe_base_ref}"
                )
                if pr_commits:
                    cherry_pick_cmds.append(
                        f"# Full commit list ({len(pr_commits)} commits) — "
                        f"cherry-pick ONLY the commits belonging to PR #{pr_num}"
                    )
                    for c in pr_commits:
                        oid = c.get("oid", "?")[:12]
                        msg = c.get("messageHeadline", "(no message)")[:80]
                        per_pr_commit_lines.append(f"{oid} {msg}")
                        cherry_pick_cmds.append(f"# git cherry-pick {_safe_sha(oid)}  # ← verify before uncommenting")
                else:
                    cherry_pick_cmds.append(
                        f"# Manually cherry-pick the commits for PR #{pr_num}"
                    )
                    per_pr_commit_lines.append("(gh unavailable — manual identification required)")
                per_pr_commit_info = get_commit_info(head_sha, base_ref, repo_root)

            elif is_shared:
                # Shared head but different bases — file diff may help.
                pr_files = fetch_pr_files(pr_num)
                if pr_files:
                    # Find commits that touch this PR's files.
                    isolated_oids = _commits_touching_files(
                        base_ref, head_sha, pr_files, repo_root
                    )
                    if isolated_oids:
                        # Get the human-readable format for report display.
                        args = ["log", "--oneline", f"{base_ref}..{head_sha}", "--"]
                        args.extend(pr_files[:50])
                        commit_text = run_git(args, repo_root).strip()
                        branch_create_cmds.append(
                            f"# Create a fresh branch from the base ref"
                        )
                        branch_create_cmds.append(
                            f"git checkout -b {safe_suggested_branch} origin/{safe_base_ref}"
                        )
                        cherry_pick_cmds.append(
                            f"# Cherry-pick only PR #{pr_num} commits "
                            f"({len(isolated_oids)} of {len(isolated_oids)} commits touch PR files)"
                        )
                        for line in (commit_text.splitlines() if commit_text else []):
                            per_pr_commit_lines.append(line)
                        for oid in isolated_oids:
                            cherry_pick_cmds.append(f"git cherry-pick {_safe_sha(oid)}")
                        per_pr_commit_info = get_commit_info(head_sha, base_ref, repo_root)
                    else:
                        # File-based isolation returned nothing — fall back to gh commit list.
                        pr_commits = fetch_pr_commits(pr_num)
                        if pr_commits:
                            branch_create_cmds.append(
                                f"# WARNING: Could not isolate PR #{pr_num} commits by file — "
                                f"using full commit list."
                            )
                            branch_create_cmds.append(
                                f"git checkout -b {safe_suggested_branch} origin/{safe_base_ref}"
                            )
                            cherry_pick_cmds.append(
                                f"# Cherry-pick commits for PR #{pr_num} ({len(pr_commits)} commits)"
                            )
                            for c in pr_commits:
                                oid = c.get("oid", "?")[:12]
                                msg = c.get("messageHeadline", "(no message)")[:80]
                                per_pr_commit_lines.append(f"{oid} {msg}")
                                cherry_pick_cmds.append(f"git cherry-pick {_safe_sha(oid)}")
                        else:
                            branch_create_cmds.append(
                                f"# WARNING: Cannot isolate commits for PR #{pr_num}. "
                                f"Manually identify this PR's commits."
                            )
                            branch_create_cmds.append(
                                f"git checkout -b {safe_suggested_branch} origin/{safe_base_ref}"
                            )
                            cherry_pick_cmds.append(
                                f"# Manually cherry-pick commits for PR #{pr_num}"
                            )
                            per_pr_commit_lines.append(
                                f"(could not determine commits — PR files: {len(pr_files)})"
                            )
                        per_pr_commit_info = {"commits": "", "diff_stat": "", "changed_files": pr_files}
                else:
                    # No PR files available — warn and fall back.
                    pr_commits = fetch_pr_commits(pr_num)
                    branch_create_cmds.append(
                        f"# WARNING: Cannot fetch PR #{pr_num} files. Commits may be shared."
                    )
                    branch_create_cmds.append(
                        f"git checkout -b {safe_suggested_branch} origin/{safe_base_ref}"
                    )
                    if pr_commits:
                        cherry_pick_cmds.append(
                            f"# Cherry-pick commits for PR #{pr_num} ({len(pr_commits)} commits)"
                        )
                        for c in pr_commits:
                            oid = c.get("oid", "?")[:12]
                            msg = c.get("messageHeadline", "(no message)")[:80]
                            per_pr_commit_lines.append(f"{oid} {msg}")
                            cherry_pick_cmds.append(f"git cherry-pick {_safe_sha(oid)}")
                    else:
                        cherry_pick_cmds.append(f"# Manually cherry-pick commits for PR #{pr_num}")
                        per_pr_commit_lines.append("(gh unavailable)")
                    per_pr_commit_info = {"commits": "", "diff_stat": "", "changed_files": []}
            else:
                # Single PR with unique head — standard path.
                pr_commits = fetch_pr_commits(pr_num)
                if pr_commits:
                    branch_create_cmds.append(
                        f"# Create a fresh branch from the base ref"
                    )
                    branch_create_cmds.append(
                        f"git checkout -b {safe_suggested_branch} origin/{safe_base_ref}"
                    )
                    cherry_pick_cmds.append(
                        f"# Cherry-pick only PR #{pr_num} commits ({len(pr_commits)} commits)"
                    )
                    for c in pr_commits:
                        oid = c.get("oid", "?")[:12]
                        msg = c.get("messageHeadline", "(no message)")[:80]
                        per_pr_commit_lines.append(f"{oid} {msg}")
                        cherry_pick_cmds.append(f"git cherry-pick {_safe_sha(oid)}")
                    last_oid = pr_commits[-1].get("oid", "")
                    base_for_diff = f"{last_oid}~{len(pr_commits)}" if len(pr_commits) > 0 else base_ref
                    per_pr_commit_info = get_commit_info(last_oid, base_for_diff, repo_root)
                else:
                    branch_create_cmds.append(
                        f"# WARNING: gh CLI unavailable — cannot determine exact commits for PR #{pr_num}"
                    )
                    branch_create_cmds.append(
                        f"# Manually identify the commits belonging to PR #{pr_num}, then:"
                    )
                    branch_create_cmds.append(f"git checkout -b {safe_suggested_branch} origin/{safe_base_ref}")
                    cherry_pick_cmds.append(
                        f"# Manually cherry-pick the commits for PR #{pr_num}"
                    )
                    cherry_pick_cmds.append(f"# git cherry-pick <commit-sha-1> <commit-sha-2> ...")
                    per_pr_commit_lines.append(
                        "(gh not available — manual identification required)"
                    )
                    per_pr_commit_info = {"commits": "", "diff_stat": "", "changed_files": []}

            # Get the current remote head SHA for the branch.
            # Use the correct remote for fork PRs, not hardcoded origin.
            if is_fork:
                # For fork PRs, query the fork remote / URL directly.
                remote_sha = run_git(
                    ["ls-remote", push_remote, f"refs/heads/{head_ref}"], repo_root
                ).strip().split()[0] if run_git(
                    ["ls-remote", push_remote, f"refs/heads/{head_ref}"], repo_root
                ).strip() else "(unknown)"
            else:
                remote_ref = f"origin/{head_ref}"
                remote_sha = run_git(
                    ["ls-remote", "origin", f"refs/heads/{head_ref}"], repo_root
                ).strip().split()[0] if run_git(
                    ["ls-remote", "origin", f"refs/heads/{head_ref}"], repo_root
                ).strip() else (
                    run_git(["rev-parse", "--verify", remote_ref], repo_root).strip() or "(unknown)"
                )

            # Generate safe force-with-lease command.
            # When manual review is required, comment out the push so the user
            # cannot accidentally push an empty/incorrect branch to the PR head.
            if requires_manual_review:
                safe_push_cmd = (
                    f"# MANUAL REVIEW REQUIRED — verify branch contents before uncommenting:\n"
                    f"# git push --force-with-lease=refs/heads/{safe_head_ref}:{_q(remote_sha)} "
                    f"{safe_push_remote} {safe_suggested_branch}:refs/heads/{safe_head_ref}"
                )
            elif is_fork:
                safe_push_cmd = (
                    f"git push --force-with-lease=refs/heads/{safe_head_ref}:{_q(remote_sha)} "
                    f"{safe_push_remote} {safe_suggested_branch}:refs/heads/{safe_head_ref}"
                )
            else:
                safe_push_cmd = (
                    f"git push --force-with-lease={_safe_ref(remote_ref)}:{_q(remote_sha)} "
                    f"{safe_push_remote} {safe_suggested_branch}:{safe_head_ref}"
                )

            plans.append({
                "pr_number": pr_num,
                "pr_title": pr.get("title", "(unknown)"),
                "pr_url": pr.get("url", ""),
                "shared_head_sha": head_sha,
                "head_ref": head_ref,
                "base_ref": base_ref,
                "base_sha": base_sha,
                "is_fork": is_fork,
                "push_remote": push_remote,
                "requires_manual_review": requires_manual_review,
                "remote_head_sha": remote_sha,
                "suggested_new_branch": suggested_branch,
                "pr_commit_count": len(per_pr_commit_lines),
                "pr_commit_list": per_pr_commit_lines,
                "fetch_commands": fetch_cmds,
                "branch_create_commands": branch_create_cmds,
                "cherry_pick_commands": cherry_pick_cmds,
                "safe_push_command": safe_push_cmd,
                "per_pr_commits": per_pr_commit_info["commits"],
                "per_pr_diff_stat": per_pr_commit_info["diff_stat"],
                "per_pr_changed_files": per_pr_commit_info["changed_files"],
            })

    return plans


# ---------------------------------------------------------------------------
# Verification checklist
# ---------------------------------------------------------------------------


def generate_verification_checklist(plans: list[dict]) -> list[str]:
    """Generate pre-push and post-push verification commands for each PR."""
    checklist: list[str] = []

    for plan in plans:
        pr_num = plan["pr_number"]
        head_ref = plan["head_ref"]
        base_ref = plan["base_ref"]
        suggested = plan["suggested_new_branch"]
        is_fork = plan.get("is_fork", False)
        push_remote = plan.get("push_remote", "origin")

        checklist.append(f"## PR #{pr_num} — {plan['pr_title']}")
        if is_fork:
            checklist.append("")
            checklist.append(f"> **Fork PR detected.** Push target: `{push_remote}`")
        checklist.append("")

        checklist.append("### Step 0: Fetch commits")
        checklist.append("")
        checklist.append("```bash")
        for cmd in plan.get("fetch_commands", ["# (no fetch needed)"]):
            checklist.append(cmd)
        checklist.append("```")
        checklist.append("")

        checklist.append("### Step 1: Create independent branch")
        checklist.append("")
        checklist.append("```bash")
        for cmd in plan.get("branch_create_commands", []):
            checklist.append(cmd)
        checklist.append("```")
        checklist.append("")

        checklist.append("### Step 2: Cherry-pick PR commits")
        checklist.append("")
        checklist.append("```bash")
        for cmd in plan.get("cherry_pick_commands", []):
            checklist.append(cmd)
        checklist.append("```")
        checklist.append("")

        checklist.append("### Step 3: Verify the new branch")
        checklist.append("")
        checklist.append("```bash")
        checklist.append(f"# Verify the new branch has only PR #{pr_num} commits")
        checklist.append(f"git log --oneline origin/{_safe_ref(base_ref)}..{_safe_ref(suggested)}")
        checklist.append("")
        checklist.append(f"# Verify files match expected set for PR #{pr_num}")
        checklist.append(f"git diff --name-only origin/{_safe_ref(base_ref)}..{_safe_ref(suggested)}")
        checklist.append("")
        checklist.append(f"# Confirm remote head SHA before force push")
        if is_fork:
            checklist.append(f"gh pr view {_safe_int(pr_num, 'PR number')} --json headRefOid,headRepository")
        else:
            checklist.append(f"git ls-remote origin refs/heads/{_safe_ref(head_ref)}")
        checklist.append("```")
        checklist.append("")

        checklist.append("### Step 4: Push")
        checklist.append("")
        if plan.get("requires_manual_review"):
            checklist.append("> **WARNING: Manual review required.** The push command is commented out.")
            checklist.append("> Review the branch contents first, then uncomment the command below.")
            checklist.append("")
        checklist.append("```bash")
        checklist.append(plan["safe_push_command"])
        checklist.append("```")
        checklist.append("")

        checklist.append("### Step 5: Post-push verification")
        checklist.append("")
        checklist.append("```bash")
        checklist.append(f"# 1. Verify PR commits")
        checklist.append(f"gh pr view {_safe_int(pr_num, 'PR number')} --json commits")
        checklist.append("")
        checklist.append(f"# 2. Verify PR files")
        checklist.append(f"gh pr view {_safe_int(pr_num, 'PR number')} --json files")
        checklist.append("")
        checklist.append(f"# 3. Verify head SHA updated")
        checklist.append(f"gh pr view {_safe_int(pr_num, 'PR number')} --json headRefOid")
        checklist.append("")
        checklist.append(f"# 4. Verify base ref unchanged")
        checklist.append(f"gh pr view {_safe_int(pr_num, 'PR number')} --json baseRefName,baseRefOid")
        checklist.append("")
        checklist.append(f"# 5. Check CI status")
        checklist.append(f"gh pr checks {_safe_int(pr_num, 'PR number')}")
        checklist.append("```")
        checklist.append("")

    return checklist


# ---------------------------------------------------------------------------
# Risk assessment & validation
# ---------------------------------------------------------------------------


def _assess_risks(plan: dict) -> list[str]:
    """Assess risk points for a single split plan.

    Checks for:
    - force-push risk (always present with --force-with-lease)
    - shared-head risk (multiple PRs share the same head SHA)
    - fork risk (push target is not origin)
    - cherry-pick conflict risk (if cherry-picking commits)
    - CI disruption risk (force push triggers CI re-run)

    Returns a list of human-readable risk descriptions.
    """
    risks: list[str] = []

    # force-push risk — always present when using --force-with-lease
    remote_head_sha = plan.get("remote_head_sha", "(unknown)")
    lease_sha = remote_head_sha if remote_head_sha and remote_head_sha != "(unknown)" else "REPLACE_WITH_LEASE_SHA"
    risks.append(
        f"Force-push risk: the push command uses --force-with-lease."
        f" If the remote HEAD for `{plan.get('head_ref', '?')}` has moved past "
        f"`{lease_sha}`, the push will be rejected. Verify the lease SHA "
        f"immediately before pushing."
    )

    # shared-head risk
    shared_head_sha = plan.get("shared_head_sha", "")
    if shared_head_sha:
        risks.append(
            f"Shared-head risk: this PR shares head SHA `{shared_head_sha[:12]}` "
            f"with one or more other PRs. Cherry-picking must be precise to avoid "
            f"including commits from other PRs."
        )

    # fork risk
    push_remote = plan.get("push_remote", "origin")
    is_fork = plan.get("is_fork", False)
    if is_fork:
        risks.append(
            f"Fork risk: push target is `{push_remote}`, not `origin`. "
            f"Ensure the fork remote is accessible and up to date before pushing."
        )

    # cherry-pick conflict risk
    pr_commit_count = plan.get("pr_commit_count", 0)
    if pr_commit_count > 0:
        risks.append(
            f"Cherry-pick conflict risk: {pr_commit_count} commit(s) will be "
            f"cherry-picked from shared history. Merge conflicts may occur if "
            f"the base branch has diverged."
        )

    # CI disruption risk — always present with force pushes
    risks.append(
        f"CI disruption risk: force-pushing to `{plan.get('head_ref', '?')}` "
        f"on `{push_remote}` will trigger a new CI run. Ensure the new head "
        f"passes all checks."
    )

    return risks


def _validate_plan_output(plans: list[dict]) -> tuple[bool, list[str]]:
    """Validate that each plan has the required output dimensions.

    Checks for non-empty values of:
    - pr_number, head_ref, base_ref (涉及 PR/Head Ref)
    - safe_push_command (verification command)
    - branch_create_commands, cherry_pick_commands (actionability)

    Returns (passed: bool, warnings: list[str]).
    """
    warnings: list[str] = []
    all_passed = True

    required_keys = [
        "pr_number",
        "head_ref",
        "base_ref",
        "safe_push_command",
    ]
    list_keys = [
        "branch_create_commands",
        "cherry_pick_commands",
    ]

    for plan in plans:
        pr_num = plan.get("pr_number", "?")

        for key in required_keys:
            val = plan.get(key)
            if not val:
                all_passed = False
                warnings.append(
                    f"PR #{pr_num}: missing or empty required field '{key}'"
                )

        for key in list_keys:
            val = plan.get(key, [])
            if not val or (isinstance(val, list) and len(val) == 0):
                all_passed = False
                warnings.append(
                    f"PR #{pr_num}: missing or empty required list field '{key}'"
                )

    return all_passed, warnings


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    branch_info: dict,
    prs: list[dict],
    shared_groups: dict[str, list[dict]],
    plans: list[dict],
    checklist: list[str],
) -> str:
    """Build the complete Markdown branch surgery report."""
    lines: list[str] = []

    def _add(text: str) -> None:
        lines.append(text)

    _add("# Branch Surgery Report")
    _add("")
    _add(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    _add("")
    _add("> **WARNING:** This is a dry-run plan. No git mutations have been executed.")
    _add("> Review all commands carefully before running them.")
    _add("")

    # --- Summary ---
    _add("## Summary")
    _add("")
    if shared_groups:
        _add(f"- **Shared head SHA detected:** {len(shared_groups)} group(s)")
        total_affected = sum(len(prs) for prs in shared_groups.values())
        _add(f"- **Affected PRs:** {total_affected}")
    else:
        _add("- No shared head SHA detected among open PRs.")
    _add(f"- **Current branch:** `{branch_info['current_branch']}`")
    _add("")

    # --- Current branch topology ---
    _add("## Current Branch Topology")
    _add("")
    _add("### Current Branch")
    _add("")
    _add(f"`{branch_info['current_branch']}`")
    _add("")

    _add("### Remotes")
    _add("")
    _add("```")
    _add(branch_info["remotes"] or "(no remotes)")
    _add("```")
    _add("")

    _add("### Commit Graph (last 30)")
    _add("")
    _add("```")
    _add(branch_info["commit_graph"] or "(no commits)")
    _add("```")
    _add("")

    if branch_info["merge_base"]:
        _add("### Merge Base")
        _add("")
        _add("```")
        _add(branch_info["merge_base"])
        _add("```")
        _add("")

    # --- PR List ---
    _add("## Open PRs")
    _add("")
    if prs:
        _add("| # | Title | Head | Base | Fork? | Head SHA |")
        _add("|---|-------|------|------|-------|----------|")
        for pr in prs:
            num = pr.get("number", "?")
            title = pr.get("title", "(no title)")[:60]
            head = pr.get("headRefName", "?")
            base = pr.get("baseRefName", "?")
            sha = pr.get("headRefOid", "?")[:8]
            is_fork_str = "yes" if pr.get("_is_fork") else "no"
            _add(f"| {num} | {title} | {head} | {base} | {is_fork_str} | {sha} |")
    else:
        _add("_(no open PRs found or gh CLI not available)_")
    _add("")

    # --- Shared Head Analysis ---
    if shared_groups:
        _add("## Shared Head SHA Analysis")
        _add("")
        for head_sha, group_prs in shared_groups.items():
            _add(f"### Head SHA: `{head_sha}`")
            _add("")
            _add("The following PRs share this head commit:")
            _add("")
            for pr in group_prs:
                _add(f"- **PR #{pr.get('number', '?')}**: {pr.get('title', '(no title)')}")
                _add(f"  - Branch: `{pr.get('headRefName', '?')}`")
                _add(f"  - URL: {pr.get('url', 'N/A')}")
            _add("")
            _add("**Problem:** Multiple PRs pointing to the same head means changes from")
            _add("one PR will appear in the other. Each PR should have its own branch")
            _add("with only its own commits.")
            _add("")

    # --- Split Plan ---
    if plans:
        _add("## Split Plan")
        _add("")
        for i, plan in enumerate(plans, 1):
            _add(f"### Plan {i}: PR #{plan['pr_number']} — {plan['pr_title']}")
            _add("")
            _add("| Field | Value |")
            _add("|-------|-------|")
            _add(f"| PR | [#{plan['pr_number']}]({plan['pr_url']}) |")
            _add(f"| Head ref | `{plan['head_ref']}` |")
            _add(f"| Base ref | `{plan['base_ref']}` |")
            _add(f"| Fork PR | {'yes' if plan.get('is_fork') else 'no'} |")
            _add(f"| Push remote | `{plan.get('push_remote', 'origin')}` |")
            _add(f"| Current remote head SHA | `{plan['remote_head_sha']}` |")
            _add(f"| Suggested new branch | `{plan['suggested_new_branch']}` |")
            _add(f"| PR-specific commits | {plan.get('pr_commit_count', '?')} |")
            _add("")

            if plan.get("pr_commit_list"):
                if plan.get("requires_manual_review"):
                    _add("#### PR Commits (shared — MANUAL REVIEW required)")
                else:
                    _add("#### PR Commits (this PR only)")
                _add("")
                _add("```")
                for line in plan["pr_commit_list"]:
                    _add(line)
                _add("```")
                _add("")

            if plan.get("per_pr_diff_stat"):
                if plan.get("requires_manual_review"):
                    _add("#### Changed Files (shared diff — may include other PRs)")
                else:
                    _add("#### Changed Files (this PR only)")
                _add("")
                _add("```")
                _add(plan["per_pr_diff_stat"])
                _add("```")
                _add("")

    # --- Per-PR Risk Assessment ---
    if plans:
        _add("## Risk Assessment")
        _add("")
        for i, plan in enumerate(plans, 1):
            _add(f"### PR #{plan['pr_number']} -- {plan['pr_title']}")
            _add("")
            risks = _assess_risks(plan)
            for risk in risks:
                _add(f"- **{risk.split(':')[0]}**: {':'.join(risk.split(':')[1:]).strip()}")
            _add("")
        _add("")

    # --- No issues found ---
    if not shared_groups:
        _add("## No Action Required")
        _add("")
        _add("No PR branch topology issues were detected. Each open PR has a unique head SHA.")
        _add("")

    # --- Verification Checklist ---
    _add("## Execution Plan & Verification Checklist")
    _add("")
    if checklist:
        for line in checklist:
            _add(line)
    else:
        _add("No execution steps needed.")
        _add("")

    # --- Rollback Plan ---
    if plans:
        _add("## Rollback Plan")
        _add("")
        _add("If any push causes issues, restore the previous state:")
        _add("")
        _add("```bash")
        for plan in plans:
            head_ref = plan.get("head_ref", "unknown")
            remote_sha = plan.get("remote_head_sha", "(unknown)")
            is_fork = plan.get("is_fork", False)
            push_remote = plan.get("push_remote", "origin")
            if is_fork:
                _add(f"# Fork PR — backup using known remote head SHA")
                _add(f"git branch backup/{_safe_ref(head_ref)}-$(date +%Y%m%d) {_q(remote_sha)}")
            else:
                _add(f"# Before surgery, create backup from origin")
                _add(f"git branch backup/{_safe_ref(head_ref)}-$(date +%Y%m%d) origin/{_safe_ref(head_ref)}")
            _add("")
        _add("# To restore a branch to its pre-surgery state:")
        for plan in plans:
            head_ref = plan.get("head_ref", "unknown")
            is_fork = plan.get("is_fork", False)
            push_remote = plan.get("push_remote", "origin")
            if is_fork:
                _add(f"# git push --force-with-lease=... {_safe_remote(push_remote)} backup/{_safe_ref(head_ref)}-<date>:refs/heads/{_safe_ref(head_ref)}")
            else:
                _add(f"# git push --force-with-lease=... origin backup/{_safe_ref(head_ref)}-<date>:{_safe_ref(head_ref)}")
        _add("```")
        _add("")

    # --- Safety Notes ---
    _add("## Safety Notes")
    _add("")
    _add("1. All push commands use `--force-with-lease` with explicit expected SHA —")
    _add("   if the remote has changed since this report was generated, the push will")
    _add("   be rejected rather than overwriting unexpected changes.")
    _add("2. This report does NOT execute any commands. You must review and run each")
    _add("   command manually or through Codex.")
    _add("3. When PRs share a base AND head, commit isolation is not possible — the plan")
    _add("   marks these with MANUAL REVIEW required and comments out both cherry-pick")
    _add("   and push commands. Review each commit before uncommenting.")
    _add("4. Consider creating a backup branch before force-pushing:")
    _add("   `git branch backup/$(git branch --show-current)-$(date +%Y%m%d)`")
    _add("5. For fork PRs, pre-push verification uses `gh pr view` instead of")
    _add("   `git ls-remote` since the fork remote may not be configured locally.")
    _add("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze PR branch topology and generate a dry-run surgery plan."
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the Markdown report "
             "(default: {artifact_dir}/branch_surgery.md).",
    )
    parser.add_argument(
        "--pr-list",
        default=None,
        help=(
            "Optional JSON array of PR objects (from gh pr list --json ...). "
            "If not provided, fetch via gh CLI automatically."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the repository root (default: current directory).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = create_parser()
    args = parser.parse_args(argv)

    # Resolve default output path if not specified.
    if args.output is None:
        args.output = str(get_artifact_dir() / "branch_surgery.md")

    repo_root = Path(args.repo_root).resolve()

    # 1. Collect PR data --------------------------------------------------
    if args.pr_list:
        try:
            prs = json.loads(args.pr_list)
        except json.JSONDecodeError:
            print("Error: invalid JSON for --pr-list", file=sys.stderr)
            sys.exit(1)
    else:
        prs = fetch_pr_list()

    if not prs:
        print(
            "Warning: No open PRs found. Report will contain only branch topology info.",
            file=sys.stderr,
        )

    # 2. Collect branch topology ------------------------------------------
    branch_info = get_branch_info(repo_root)

    # 3. Annotate PRs with fork status (for report table) -----------------
    for pr in prs:
        pr["_is_fork"] = _is_fork_pr(pr, repo_root)

    # 4. Detect shared heads ----------------------------------------------
    shared_groups = find_shared_heads(prs)

    # 5. Generate split plans ---------------------------------------------
    plans = generate_split_plan(shared_groups, repo_root)

    # 4.5 Validate plan output dimensions ----------------------------------
    if plans:
        passed, warnings = _validate_plan_output(plans)
        if not passed:
            for w in warnings:
                print(f"[deepseek-forge] WARNING: {w}", file=sys.stderr)

    # 5. Generate verification checklist ----------------------------------
    checklist = generate_verification_checklist(plans) if plans else []

    # 6. Build and write report -------------------------------------------
    report = generate_report(branch_info, prs, shared_groups, plans, checklist)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"Branch surgery report written to {args.output}")
    print(f"  PRs analyzed: {len(prs)}")
    print(f"  Shared head groups: {len(shared_groups)}")
    print(f"  Split plans: {len(plans)}")


if __name__ == "__main__":
    main()
