#!/usr/bin/env python3
"""Collect repository context into a single Markdown file for DeepSeek consumption.

Has two modes (selectable via ``--mode``):

*default* (full context for patch generation)
    - The task description
    - Git status and diff stat
    - A directory tree of tracked files
    - Key configuration file contents
    - Relevant source file contents (prioritized by keyword match against the task)
    - A summary section with inclusion metrics

*pr-branch-topology* (lightweight, Git/PR metadata only)
    - The task description
    - Git status, current branch, remote list, commit graph, commit history
    - Changed files summary and merge-base info
    - PR information from GitHub (via ``gh`` CLI)
    - A topology summary section

    No source code or config files are included.

Environment variables:
    ``DEEPSEEK_FORGE_ARTIFACT_DIR``
        Override the default output directory.  When set, ``--output``
        defaults to ``{DEEPSEEK_FORGE_ARTIFACT_DIR}/repo_context.md``.

Uses only stdlib — no external dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from forge_config import get_artifact_dir


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Individual file size cap before we refuse to include it.
_INDIVIDUAL_FILE_MAX_BYTES = 200_000  # 200 KB

# Number of initial bytes read from a file to sniff its binary-ness.
_BINARY_SNIFF_BYTES = 1024

# Config files we always try to include when they exist in the repo.
_CONFIG_FILE_NAMES: set[str] = {
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "tsconfig.json",
    "Makefile",
    "CMakeLists.txt",
    "pom.xml",
    "build.gradle",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
}

# Patterns whose match against a file *path* signals that we should skip it.
#
# Keep this list in priority order: longer / more-specific alternatives
# before shorter suffixes so the regex engine picks the right match.
_SKIP_PATTERNS: list[str] = [
    # Lock files
    r"(^|/)package-lock\.json$",
    r"(^|/)pnpm-lock\.yaml$",
    r"(^|/)yarn\.lock$",
    r"(^|/)Cargo\.lock$",
    r"(^|/)go\.sum$",
    r"(^|/)poetry\.lock$",
    r"(^|/)Gemfile\.lock$",
    r"(^|/)Pipfile\.lock$",
    # VCS
    r"(^|/)\.git/",
    # Build artifacts
    r"(^|/)dist/",
    r"(^|/)build/",
    r"(^|/)node_modules/",
    r"(^|/)__pycache__/",
    r"\.pyc$",
    r"(^|/)target/",
    r"\.class$",
    r"\.o$",
    r"\.so$",
    r"\.dylib$",
]

_SKIP_RE = re.compile("|".join(_SKIP_PATTERNS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_binary_file(filepath: Path) -> bool:
    """Return True if *filepath* appears to be a binary file.

    We read the first 1024 bytes; if a null byte (``b'\\x00'``) is present
    the file is treated as binary.
    """
    try:
        data = filepath.read_bytes()[: _BINARY_SNIFF_BYTES]
    except (OSError, PermissionError):
        return True  # unreadable files are treated as binary / skipped
    return b"\x00" in data


def is_lock_file(filepath: Path) -> bool:
    """Return True if *filepath* is a well-known lock file."""
    name = filepath.name
    return name in {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.lock",
        "go.sum",
        "poetry.lock",
        "Gemfile.lock",
        "Pipfile.lock",
    }


def is_build_artifact(filepath: Path) -> bool:
    """Return True if the path matches a build-artifact pattern."""
    return bool(_SKIP_RE.search(str(filepath)))


def should_skip(filepath: Path) -> tuple[bool, str | None]:
    """Return (skip, reason).

    Checks: .git, lock files, build artifacts, binary, file size.
    """
    path_str = str(filepath)

    if ".git" in filepath.parts:
        return True, ".git path"
    if is_lock_file(filepath):
        return True, "lock file"
    if is_build_artifact(filepath):
        return True, "build artifact"
    if not filepath.is_file():
        return True, "not a regular file"

    try:
        size = filepath.stat().st_size
    except OSError:
        return True, "cannot stat"

    if size > _INDIVIDUAL_FILE_MAX_BYTES:
        return True, f"file too large ({size} > {_INDIVIDUAL_FILE_MAX_BYTES})"
    if is_binary_file(filepath):
        return True, "binary file"

    return False, None


def run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout, or an error marker string."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=30,
        )
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def run_gh(args: list[str], cwd: Path) -> str:
    """Run a gh CLI command and return stdout, or an empty string on error."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=30,
        )
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def extract_keywords(text: str) -> list[str]:
    """Extract lowercase alphanumeric keywords from *text*."""
    return [w.lower() for w in _WORD_RE.findall(text)]


def score_file(filepath: Path, keywords: list[str]) -> int:
    """Return a relevance score: number of keyword matches in the path (case-insensitive)."""
    path_lower = str(filepath).lower()
    return sum(1 for kw in keywords if kw in path_lower)


# ---------------------------------------------------------------------------
# File tree
# ---------------------------------------------------------------------------

def build_file_tree(paths: list[Path], root: Path) -> list[str]:
    """Return a list of indented lines representing the directory tree of *paths*.

    Each *path* is relative to *root*.  Directories are collapsed so that a
    directory that contains only a single child is presented as ``dir/file``
    rather than nesting.
    """
    # Build a nested dict: {"dir": {..., "_files": [...]}}
    tree: dict[str, list[str] | dict] = {}
    root_depth = len(root.resolve().parts)

    for p in sorted(paths):
        try:
            # Normalize: force-relative to root, drop leading "./"
            rel = str(p.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = str(p)

        parts = Path(rel).parts
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}  # type: ignore[index]
            node = node[part]  # type: ignore[assignment,index]
        # Leaf
        node.setdefault("_files", []).append(parts[-1])  # type: ignore[union-attr,index]

    lines: list[str] = []

    def _walk(node: dict, prefix: str, depth: int) -> None:
        # Separate dirs and files
        dirs = sorted(k for k in node if k != "_files")
        files = sorted(node.get("_files", []))  # type: ignore[arg-type]
        count = len(dirs) + len(files)

        for i, d in enumerate(dirs):
            is_last = (i == count - 1) if not files else False
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{d}/")
            child_prefix = prefix + ("    " if is_last else "│   ")
            _walk(node[d], child_prefix, depth + 1)  # type: ignore[arg-type]

        for i, f in enumerate(files):
            is_last = i == len(files) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{f}")

    _walk(tree, "", 0)
    return lines


# ---------------------------------------------------------------------------
# Output generation
# ---------------------------------------------------------------------------

_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".hh": "cpp",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".css": "css",
    ".html": "html",
    ".sql": "sql",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".gradle": "groovy",
    ".cfg": "ini",
    ".ini": "ini",
    ".tf": "hcl",
    ".dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "cmakelists.txt": "cmake",
}


def _lang_for(path: Path) -> str:
    """Guess a Markdown code-fence language from the file extension or name."""
    name_lower = path.name.lower()
    if name_lower in _LANGUAGE_MAP:
        return _LANGUAGE_MAP[name_lower]
    suffix = path.suffix.lower()
    return _LANGUAGE_MAP.get(suffix, "")


def _read_file(path: Path) -> str | None:
    """Return file contents as a string or None if unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None


def generate_output(
    task_content: str,
    git_status: str,
    git_diff: str,
    tree_lines: list[str],
    config_files: dict[str, str],
    source_entries: list[tuple[Path, str, int]],  # (path, content, score)
    files_considered: int,
    files_skipped: dict[str, int],  # reason -> count
    max_bytes: int,
) -> str:
    """Build the complete Markdown output string.

    Builds the full output first, then enforces *max_bytes* by byte-level
    truncation.  If truncation occurs, a notice is appended and the summary
    is updated to reflect the fact.
    """
    lines: list[str] = []

    def _add(text: str) -> None:
        lines.append(text)

    # --- Header & Task ---
    _add("# Repository Context\n\n")
    _add("## Task Description\n\n")
    _add(task_content.rstrip() + "\n\n")

    # --- Git Status ---
    _add("## Git Status\n\n")
    if git_status.strip():
        _add("```\n" + git_status.rstrip() + "\n```\n\n")
    else:
        _add("```\n(nothing to commit, working tree clean or git not available)\n```\n\n")

    # --- Git Diff Stat ---
    _add("## Git Diff Stat\n\n")
    if git_diff.strip():
        _add("```\n" + git_diff.rstrip() + "\n```\n\n")
    else:
        _add("```\n(no staged/unstaged changes or git not available)\n```\n\n")

    # --- File Tree ---
    _add("## File Tree\n\n")
    _add("```\n")
    _add(".\n")
    for tree_line in tree_lines:
        _add(tree_line + "\n")
    _add("```\n\n")

    # --- Configuration Files ---
    if config_files:
        _add("## Configuration Files\n\n")
        for fname in sorted(config_files):
            content = config_files[fname]
            if content is None:
                _add(f"### {fname}\n\n```\n(File not readable)\n```\n\n")
                continue
            lang = _lang_for(Path(fname))
            _add(f"### {fname}\n\n")
            _add(f"```{lang}\n{content.rstrip()}\n```\n\n")

    # --- Source Files ---
    _add("## Source Files (task-related)\n\n")
    files_included = 0
    for src_path, content, score in source_entries:
        rel_name = str(src_path)
        lang = _lang_for(src_path)
        _add(f"### {rel_name}\n\n")
        if content is None:
            _add("```\n(File not readable)\n```\n\n")
            continue
        _add(f"```{lang}\n{content.rstrip()}\n```\n\n")
        files_included += 1

    # --- Summary ---
    skip_reasons_str = "; ".join(
        f"{reason}: {count}" for reason, count in sorted(files_skipped.items())
    ) or "none"

    # Build the full text without truncation first.
    full_text = "".join(lines)
    total_bytes = len(full_text.encode("utf-8"))
    truncated = False

    if total_bytes > max_bytes:
        truncated = True
        # Truncate to max_bytes minus room for the truncation note + summary.
        truncation_note_template = (
            "\n\n> **Note:** Output was truncated to stay within the "
            f"--max-bytes limit ({max_bytes} bytes). "
            "Some files may be incomplete or omitted.\n"
        )
        summary_overhead = 500  # bytes for the summary section
        note_bytes = len(truncation_note_template.encode("utf-8"))
        cutoff = max(0, max_bytes - note_bytes - summary_overhead)

        # Truncate at a clean byte boundary (end of the last full UTF-8 char).
        raw = full_text.encode("utf-8")[:cutoff]
        # Drop any incomplete trailing multibyte sequence.
        try:
            full_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Remove trailing bytes until valid.
            for trim in range(1, 5):
                try:
                    full_text = raw[:-trim].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    continue

    # Append the summary + optional truncation note.
    summary_parts: list[str] = []
    summary_parts.append("\n\n## Context Summary\n\n")
    summary_parts.append(f"- Files considered: {files_considered}\n")
    summary_parts.append(f"- Files included: {files_included}\n")
    summary_parts.append(
        f"- Files skipped: {sum(files_skipped.values())} ({skip_reasons_str})\n"
    )
    summary_parts.append(f"- Total bytes: {total_bytes}\n")
    summary_parts.append(f"- Truncated: {'yes' if truncated else 'no'}\n")

    if truncated:
        summary_parts.append(truncation_note_template)

    return full_text + "".join(summary_parts)


# ---------------------------------------------------------------------------
# PR branch topology output generation
# ---------------------------------------------------------------------------

def generate_pr_topology_output(
    task_content: str,
    git_status: str,
    git_branch: str,
    git_remote: str,
    git_log_graph: str,
    git_log: str,
    git_diff_stat: str,
    git_merge_base: str,
    pr_list: str,
    pr_details: str,
    max_bytes: int,
) -> str:
    """Build a lightweight Markdown output with Git/PR metadata only.

    No source code or config files are included — purely branch topology and
    PR metadata for a ``pr-branch-topology`` task.
    """
    lines: list[str] = []

    def _add(text: str) -> None:
        lines.append(text)

    # --- Header & Task ---
    _add("# PR Branch Topology Context\n\n")
    _add("## Task Description\n\n")
    _add(task_content.rstrip() + "\n\n")

    # --- Git Status ---
    _add("## Git Status\n\n")
    if git_status.strip():
        _add("```\n" + git_status.rstrip() + "\n```\n\n")
    else:
        _add("```\n(working tree clean or git not available)\n```\n\n")

    # --- Current Branch ---
    _add("## Current Branch\n\n")
    _add(f"```\n{git_branch.strip() or '(unknown)'}\n```\n\n")

    # --- Remote List ---
    _add("## Remote List\n\n")
    if git_remote.strip():
        _add("```\n" + git_remote.rstrip() + "\n```\n\n")
    else:
        _add("```\n(no remotes configured or git not available)\n```\n\n")

    # --- Commit Graph ---
    _add("## Commit Graph\n\n")
    if git_log_graph.strip():
        _add("```\n" + git_log_graph.rstrip() + "\n```\n\n")
    else:
        _add("```\n(git log --graph not available)\n```\n\n")

    # --- PR Information ---
    _add("## PR Information\n\n")
    if pr_list.strip():
        _add("### PR List (JSON)\n\n")
        _add("```json\n" + pr_list.rstrip() + "\n```\n\n")
        if pr_details.strip():
            _add("### PR Details\n\n")
            _add(pr_details.rstrip() + "\n\n")
    else:
        _add("```\n(gh CLI not available or no PRs found)\n```\n\n")

    # --- Commit History ---
    _add("## Commit History\n\n")
    if git_log.strip():
        _add("```\n" + git_log.rstrip() + "\n```\n\n")
    else:
        _add("```\n(git log not available)\n```\n\n")

    # --- Changed Files Summary ---
    _add("## Changed Files Summary\n\n")
    if git_diff_stat.strip():
        _add("```\n" + git_diff_stat.rstrip() + "\n```\n\n")
    else:
        _add("```\n(diff stat not available)\n```\n\n")

    # --- Merge Base Info ---
    _add("## Merge Base Info\n\n")
    if git_merge_base.strip():
        _add("```\n" + git_merge_base.rstrip() + "\n```\n\n")
    else:
        _add("```\n(merge-base info not available)\n```\n\n")

    # --- Truncation handling ---
    full_text = "".join(lines)
    total_bytes = len(full_text.encode("utf-8"))
    truncated = False

    if total_bytes > max_bytes:
        truncated = True
        truncation_note_template = (
            "\n\n> **Note:** Output was truncated to stay within the "
            f"--max-bytes limit ({max_bytes} bytes). "
            "Some information may be incomplete or omitted.\n"
        )
        summary_overhead = 500
        note_bytes = len(truncation_note_template.encode("utf-8"))
        cutoff = max(0, max_bytes - note_bytes - summary_overhead)

        raw = full_text.encode("utf-8")[:cutoff]
        try:
            full_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            for trim in range(1, 5):
                try:
                    full_text = raw[:-trim].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    continue

    # --- Topology Summary ---
    summary_parts: list[str] = []
    summary_parts.append("\n\n## Topology Summary\n\n")
    summary_parts.append(f"- Total bytes: {total_bytes}\n")
    summary_parts.append(f"- Truncated: {'yes' if truncated else 'no'}\n")

    if truncated:
        summary_parts.append(truncation_note_template)

    return full_text + "".join(summary_parts)


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Collect repository context into a single Markdown file."
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Path to the task file (Markdown) whose content is embedded at the top.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path where the generated Markdown file will be written "
             "(default: $DEEPSEEK_FORGE_ARTIFACT_DIR/repo_context.md or "
             "/tmp/deepseek-forge-{session?}-{pid}/repo_context.md).",
    )
    parser.add_argument(
        "--mode",
        choices=["default", "pr-branch-topology"],
        default="default",
        help="Operation mode: 'default' collects full source context, "
             "'pr-branch-topology' collects Git/PR metadata only "
             "(default: %(default)s).",
    )
    # Defaults vary with 1M context mode
    _enable_1m = os.environ.get("DEEPSEEK_ENABLE_1M_CONTEXT", "true").lower() in ("true", "1")
    _default_max_files = 200 if _enable_1m else 80
    _default_max_bytes = 500_000 if _enable_1m else 120_000

    parser.add_argument(
        "--max-files",
        type=int,
        default=_default_max_files,
        help="Maximum number of source files to include (default: %(default)s).",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=_default_max_bytes,
        help="Maximum total output bytes (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    # Resolve default output path from env var / config.
    if args.output is None:
        args.output = str(get_artifact_dir() / "repo_context.md")

    repo_root = Path.cwd()

    # 1. Read the task file -----------------------------------------------
    task_path = Path(args.task)
    if not task_path.is_file():
        print(f"error: task file not found: {task_path}", file=sys.stderr)
        sys.exit(1)
    task_content = task_path.read_text(encoding="utf-8", errors="replace")

    # 2. Git status (used by both modes) ---------------------------------
    git_status = run_git(["status", "--short"], repo_root)

    if args.mode == "pr-branch-topology":
        # ---------------------------------------------------------------
        # PR Branch Topology Mode — Git/PR metadata only, no source code
        # ---------------------------------------------------------------

        git_branch = run_git(["branch", "--show-current"], repo_root).strip()
        git_remote = run_git(["remote", "-v"], repo_root)
        git_log_graph = run_git(
            ["log", "--oneline", "--graph", "--all", "-20"], repo_root
        )
        git_log = run_git(["log", "--oneline", "-20"], repo_root)
        git_diff_stat = run_git(
            ["diff", "--stat", "HEAD~10..HEAD"], repo_root
        )

        # Merge-base info: list local branches and their merge-base with HEAD.
        merge_base_lines: list[str] = []
        branches_out = run_git(["branch"], repo_root)
        if branches_out.strip():
            for line in branches_out.strip().splitlines():
                branch = line.strip().lstrip("*").strip()
                if branch:
                    mb = run_git(
                        ["merge-base", "HEAD", branch], repo_root
                    ).strip()
                    if mb:
                        merge_base_lines.append(
                            f"merge-base HEAD..{branch}: {mb}"
                        )
        git_merge_base = "\n".join(merge_base_lines)

        # PR info via gh CLI.
        pr_list = run_gh(
            [
                "pr", "list",
                "--json", "number,title,headRefName,baseRefName,headRefOid,baseRefOid,headRepository,headRepositoryOwner",
            ],
            repo_root,
        )
        pr_details = ""
        if pr_list.strip():
            try:
                pr_data = json.loads(pr_list)
                for pr_entry in pr_data:
                    number = pr_entry.get("number", "")
                    pr_details += (
                        f"### PR #{number}: {pr_entry.get('title', '')}\n"
                    )
                    pr_details += (
                        f"- Head: {pr_entry.get('headRefName', '')} "
                        f"({pr_entry.get('headRefOid', '')})\n"
                    )
                    pr_details += (
                        f"- Base: {pr_entry.get('baseRefName', '')} "
                        f"({pr_entry.get('baseRefOid', '')})\n"
                    )
                    commits = run_gh(
                        [
                            "pr", "view", str(number),
                            "--json", "commits",
                            "-q", ".commits[].oid",
                        ],
                        repo_root,
                    )
                    if commits.strip():
                        pr_details += f"- Commits:\n{commits}"
                    files = run_gh(
                        [
                            "pr", "view", str(number),
                            "--json", "files",
                            "-q", ".files[].path",
                        ],
                        repo_root,
                    )
                    if files.strip():
                        pr_details += f"- Changed files:\n{files}"
                    pr_details += "\n\n"
            except json.JSONDecodeError:
                pr_details = "(could not parse PR data)"

        output = generate_pr_topology_output(
            task_content=task_content,
            git_status=git_status,
            git_branch=git_branch,
            git_remote=git_remote,
            git_log_graph=git_log_graph,
            git_log=git_log,
            git_diff_stat=git_diff_stat,
            git_merge_base=git_merge_base,
            pr_list=pr_list,
            pr_details=pr_details,
            max_bytes=args.max_bytes,
        )

    else:
        # ---------------------------------------------------------------
        # Default Mode — full repository context
        # ---------------------------------------------------------------

        # 3. Git diff stat --------------------------------------------------
        git_diff = run_git(["diff", "--stat"], repo_root)

        # 4. Enumerate tracked files ----------------------------------------
        ls_files_out = run_git(["ls-files"], repo_root)
        tracked_paths: list[Path] = []
        if ls_files_out.strip():
            for line in ls_files_out.strip().splitlines():
                p = (repo_root / line.strip()).resolve()
                try:
                    p.relative_to(repo_root.resolve())
                except ValueError:
                    continue
                tracked_paths.append(p)
        else:
            for root, dirs, filenames in os.walk(str(repo_root)):
                if ".git" in Path(root).parts:
                    continue
                for fname in filenames:
                    tracked_paths.append(Path(root) / fname)

        # 5. Classify every tracked file ------------------------------------
        files_skipped: dict[str, int] = defaultdict(int)
        config_contents: dict[str, str] = {}
        source_candidates: list[tuple[Path, int]] = []
        files_considered = len(tracked_paths)

        keywords = extract_keywords(task_content)

        for fp in tracked_paths:
            skip, reason = should_skip(fp)
            if skip:
                files_skipped[reason] += 1
                continue

            if fp.name in _CONFIG_FILE_NAMES:
                config_contents[fp.name] = _read_file(fp) or "(unreadable)"
                files_skipped["config file (included separately)"] += 1
                continue

            score = score_file(fp, keywords)
            source_candidates.append((fp, score))

        # 6. Sort source files by relevance (desc), then path.
        source_candidates.sort(key=lambda x: (-x[1], str(x[0])))

        # 7. Apply --max-files limit.
        max_files = args.max_files
        selected_sources = source_candidates[:max_files]
        files_skipped["max-files limit"] += max(
            0, len(source_candidates) - max_files
        )

        source_entries: list[tuple[Path, str, int]] = []
        for fp, score in selected_sources:
            content = _read_file(fp)
            source_entries.append((fp, content or "(unreadable)", score))

        # 8. Build the file tree --------------------------------------------
        tree_paths: list[Path] = []
        for fp in tracked_paths:
            skip, _ = should_skip(fp)
            if not skip or fp.name in _CONFIG_FILE_NAMES:
                tree_paths.append(fp)
        tree_lines = build_file_tree(tree_paths, repo_root)

        # 9. Generate markdown output ---------------------------------------
        output = generate_output(
            task_content=task_content,
            git_status=git_status,
            git_diff=git_diff,
            tree_lines=tree_lines,
            config_files=config_contents,
            source_entries=source_entries,
            files_considered=files_considered,
            files_skipped=dict(files_skipped),
            max_bytes=args.max_bytes,
        )

    # 10. Write output file -------------------------------------------------
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")

    print(f"Wrote {len(output.encode('utf-8'))} bytes to {output_path}")


if __name__ == "__main__":
    main()
