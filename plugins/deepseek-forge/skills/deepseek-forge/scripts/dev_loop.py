#!/usr/bin/env python3
"""Forward development loop orchestrator for deepseek-forge.

Reads a task description (or existing ``plan.md`` / ``todo.md``), expands it
into acceptance criteria + plan + todos via DeepSeek, then iterates:

1. Implement pending todos (parallel sub-agents)
2. Review candidate patches
3. Apply patches safely (--check gate)
4. Run project checks
5. If checks fail: write bugs, fix, re-check
6. Final acceptance review

DeepSeek produces diffs and JSON reports.  Codex is the sole executor that
applies patches, runs checks, and judges bugs/acceptance.

Environment variables override defaults (see :mod:`forge_config`).

Usage::

    python3 dev_loop.py \\
        --task task.md \\
        --model deepseek-v4-pro \\
        [--output-dir /tmp/my-run] \\
        [--max-loops 5]

Uses only stdlib — no external dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure sibling scripts are importable.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from forge_config import get_artifact_dir, get_max_loops, get_max_parallel_agents
from state import (
    LoopState,
    TodoItem,
    BugItem,
    PatchRecord,
    CheckResult,
    create_state,
    save_state,
    load_state,
    get_pending_todos,
    get_open_bugs,
    add_bug,
    close_bug,
    mark_todo_status,
    all_todos_done,
    record_patch,
    should_stop_loop,
    should_split_todo,
    is_patch_too_large,
    has_failure_oscillation,
    record_failure,
    _failure_signature,
)


# ---------------------------------------------------------------------------
# DeepSeek API helpers (copied / adapted from deepseek_worker.py for standalone use)
# ---------------------------------------------------------------------------

def _resolve_template_path() -> Path:
    """Locate ``references/prompt_templates.md``."""
    from forge_config import get_forge_home
    path = get_forge_home() / "references" / "prompt_templates.md"
    if path.is_file():
        return path
    fallback = _SCRIPT_DIR.parent / "references" / "prompt_templates.md"
    return fallback


_TEMPLATE_PATH = _resolve_template_path()


def _read_template(template_name: str) -> str:
    """Extract a named template section from the templates file."""
    import re
    content = _TEMPLATE_PATH.read_text(encoding="utf-8", errors="replace")
    escaped = re.escape(template_name)
    heading_pat = re.compile(
        r"^#{2,4}\s+Template:\s+`?" + escaped + r"`?", re.MULTILINE
    )
    match = heading_pat.search(content)
    if match is None:
        raise ValueError(f"Template '{template_name}' not found")
    start = match.end()
    if start < len(content) and content[start] == "\n":
        start += 1
    next_heading = re.compile(r"^#{2,4}\s+Template:", re.MULTILINE)
    next_match = next_heading.search(content, start)
    end = next_match.start() if next_match else len(content)
    return content[start:end].strip()


def _call_deepseek(
    model: str,
    system_prompt: str,
    user_message: str,
    api_key: str | None = None,
    endpoint: str = "https://api.deepseek.com/chat/completions",
    temperature: float = 0.2,
    timeout: int = 180,
) -> str:
    """Call the DeepSeek API and return the assistant's text response."""
    import json as _json
    import urllib.error
    import urllib.request

    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")

    body = _json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Diff extraction (from deepseek_worker.py)
# ---------------------------------------------------------------------------

def _extract_diff(response_text: str) -> str:
    """Extract and validate a unified diff from a model response."""
    lines = response_text.splitlines()

    # Remove markdown fences
    start_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") and start_idx is None:
            start_idx = i
        elif stripped == "```" and start_idx is not None:
            end_idx = i
            break

    if start_idx is not None:
        raw = "\n".join(lines[start_idx + 1:end_idx]).strip() if end_idx is not None else "\n".join(lines[start_idx + 1:]).strip()
    else:
        raw = response_text.strip()

    raw_lines = raw.splitlines()

    first_diff = None
    for i, line in enumerate(raw_lines):
        if line.startswith("--- "):
            first_diff = i
            break

    if first_diff is None:
        raise ValueError("Response contains no valid unified diff")

    def _is_diff_line(line: str) -> bool:
        if not line:
            return True
        return line[0] in ("+", "-", " ", "@", "\\")

    last_diff = first_diff
    for i in range(len(raw_lines) - 1, first_diff - 1, -1):
        if _is_diff_line(raw_lines[i]):
            last_diff = i
            break

    diff_lines = raw_lines[first_diff:last_diff + 1]
    result = "\n".join(diff_lines).strip()

    if not result or "--- " not in result:
        raise ValueError("Response contains no valid unified diff")

    return result


def _extract_json(response_text: str) -> dict:
    """Extract a JSON object from a model response (handles fences)."""
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```"):
        text = text[len("```"):].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Plan expansion
# ---------------------------------------------------------------------------

def expand_plan(
    task: str,
    context: str = "",
    model: str = "deepseek-v4-pro",
) -> dict:
    """Call DeepSeek to expand a task into acceptance criteria, plan, and todos."""
    template = _read_template("expand_plan")
    user = f"# Task\n\n{task}"
    if context:
        user += f"\n\n# Repository Context\n\n{context}"
    response = _call_deepseek(model, template, user)
    return _extract_json(response)


# ---------------------------------------------------------------------------
# Todo implementation (single)
# ---------------------------------------------------------------------------

def implement_todo(
    todo: TodoItem,
    acceptance: list[str],
    context: str,
    state_json: str = "",
    model: str = "deepseek-v4-pro",
) -> str:
    """Call DeepSeek to generate a patch for a single todo item."""
    template = _read_template("implement_todo")
    user = (
        f"# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Todo Item\n\n"
        f"**ID:** {todo.id}\n"
        f"**Title:** {todo.title}\n"
        f"**Description:** {todo.description}\n"
        f"**Files:** {', '.join(todo.files)}\n"
        f"\n# Repository Context\n\n{context}"
    )
    if state_json:
        user += f"\n\n# Current State\n\n{state_json}"
    response = _call_deepseek(model, template, user)
    return _extract_diff(response)


# ---------------------------------------------------------------------------
# Review candidate patch
# ---------------------------------------------------------------------------

def review_candidate_patch(
    todo: TodoItem,
    acceptance: list[str],
    patch: str,
    context: str = "",
    model: str = "deepseek-v4-pro",
) -> dict:
    """Call DeepSeek to review a candidate implementation patch."""
    template = _read_template("review_candidate_patch")
    user = (
        f"# Todo Item\n\n"
        f"**ID:** {todo.id}\n**Title:** {todo.title}\n**Description:** {todo.description}\n"
        f"\n# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Candidate Patch\n\n{patch}"
    )
    if context:
        user += f"\n\n# Repository Context\n\n{context}"
    response = _call_deepseek(model, template, user)
    return _extract_json(response)


# ---------------------------------------------------------------------------
# Write tests for todo
# ---------------------------------------------------------------------------

def write_tests_for_todo(
    todo: TodoItem,
    acceptance: list[str],
    implementation_patch: str,
    context: str = "",
    model: str = "deepseek-v4-pro",
) -> str:
    """Call DeepSeek to generate tests for a todo."""
    template = _read_template("write_tests_for_todo")
    user = (
        f"# Todo Item\n\n"
        f"**ID:** {todo.id}\n**Title:** {todo.title}\n**Description:** {todo.description}\n"
        f"\n# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Implementation Patch\n\n{implementation_patch}"
    )
    if context:
        user += f"\n\n# Repository Context\n\n{context}"
    response = _call_deepseek(model, template, user)
    return _extract_diff(response)


# ---------------------------------------------------------------------------
# Fix open bugs
# ---------------------------------------------------------------------------

def fix_open_bugs(
    bugs: list[BugItem],
    acceptance: list[str],
    context: str,
    patch_history: str = "",
    model: str = "deepseek-v4-pro",
) -> str:
    """Call DeepSeek to generate a fix patch for open bugs."""
    template = _read_template("fix_open_bugs")
    bugs_text = "\n".join(
        f"- **{b.id}** [{b.severity}] {b.title}\n  {b.description}"
        for b in bugs
    )
    user = (
        f"# Open Bugs\n\n{bugs_text}\n\n"
        f"# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance)
    )
    if context:
        user += f"\n\n# Repository Context\n\n{context}"
    if patch_history:
        user += f"\n\n# Patch History\n\n{patch_history}"
    response = _call_deepseek(model, template, user)
    return _extract_diff(response)


# ---------------------------------------------------------------------------
# Final acceptance review
# ---------------------------------------------------------------------------

def final_acceptance_review(
    acceptance: list[str],
    full_diff: str,
    check_log: str,
    model: str = "deepseek-v4-pro",
) -> dict:
    """Call DeepSeek for final acceptance assessment."""
    template = _read_template("final_acceptance_review")
    user = (
        f"# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Full Implementation Diff\n\n{full_diff}\n\n"
        f"# Check Results\n\n{check_log}"
    )
    response = _call_deepseek(model, template, user)
    return _extract_json(response)


# ---------------------------------------------------------------------------
# Local script runners (Codex-executed operations)
# ---------------------------------------------------------------------------

def _apply_patch_check(patch_path: str) -> tuple[bool, str]:
    """Run ``apply_patch_safe.py --check``.  Returns (passed, output)."""
    script = _SCRIPT_DIR / "apply_patch_safe.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--patch", patch_path, "--check"],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def _apply_patch(patch_path: str) -> tuple[bool, str]:
    """Run ``apply_patch_safe.py --apply``.  Returns (passed, output)."""
    script = _SCRIPT_DIR / "apply_patch_safe.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--patch", patch_path, "--apply"],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def _run_checks(state: LoopState) -> tuple[bool, str]:
    """Run project checks and return (all_passed, output_log)."""
    check_script = _SCRIPT_DIR / "run_checks.sh"
    try:
        result = subprocess.run(
            ["bash", str(check_script)],
            capture_output=True, text=True, timeout=300,
            cwd=state.repo_root,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)

    output = result.stdout + "\n" + result.stderr
    # Write check log
    log_path = get_artifact_dir() / f"check_{state.loop_index}.log"
    log_path.write_text(output, encoding="utf-8")

    passed = result.returncode == 0
    state.check_results.append(CheckResult(
        loop_index=state.loop_index,
        command=f"bash {check_script}",
        exit_code=result.returncode,
        output_path=str(log_path),
        passed=passed,
    ))
    return passed, output


def _get_diff_since_base(state: LoopState) -> str:
    """Get the full diff since the base commit."""
    try:
        result = subprocess.run(
            ["git", "diff", state.base_sha],
            capture_output=True, text=True, timeout=30,
            cwd=state.repo_root,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return ""


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_forward_development_loop(
    task: str,
    context: str = "",
    model: str = "deepseek-v4-pro",
    state: LoopState | None = None,
) -> LoopState:
    """Execute the full forward development loop.

    Parameters
    ----------
    task : str
        The task description.
    context : str
        Pre-collected repository context (if empty, a minimal snapshot is used).
    model : str
        DeepSeek model name.
    state : LoopState or None
        Resume from an existing state, or create a fresh one.

    Returns
    -------
    LoopState
        The final state after the loop completes or stops.
    """
    artifact_dir = get_artifact_dir()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if state is None:
        state = create_state()
    state.status = "planning"
    save_state(state)

    max_parallel = state.max_parallel_agents

    # ---- Phase 1: Expand plan -------------------------------------------
    print(f"[dev_loop] Loop {state.loop_index}: expanding plan...")
    try:
        expansion = expand_plan(task, context, model)
        state.acceptance = expansion.get("acceptance", [])
        state.plan = expansion.get("plan", "")
        todo_dicts = expansion.get("todos", [])
        state.todos = [
            TodoItem(
                id=t.get("id", f"todo-{i + 1}"),
                title=t.get("title", ""),
                description=t.get("description", ""),
                files=t.get("files", []),
                status="pending",
            )
            for i, t in enumerate(todo_dicts)
        ]
    except Exception as e:
        print(f"[dev_loop] ERROR: Plan expansion failed: {e}", file=sys.stderr)
        state.status = "failed"
        save_state(state)
        return state

    # Write human-readable artifacts
    _write_acceptance_md(state, artifact_dir)
    _write_plan_md(state, artifact_dir)
    _write_todo_md(state, artifact_dir)

    save_state(state)

    # ---- Phase 2: Development loop -------------------------------------
    while True:
        stop, reason = should_stop_loop(state)
        if stop:
            print(f"[dev_loop] Stopping: {reason}")
            state.status = "failed"
            save_state(state)
            return state

        pending = get_pending_todos(state)
        if not pending and not get_open_bugs(state):
            print("[dev_loop] All todos done, no open bugs.")
            break

        state.loop_index += 1
        state.status = "implementing"
        print(f"[dev_loop] --- Loop {state.loop_index}/{state.max_loops} ---")

        # ---- Phase 2a: Implement pending todos (parallel) --------------
        if pending:
            _implement_todos_parallel(state, pending, context, model, max_parallel)

        # ---- Phase 2b: Review & apply patches --------------------------
        _review_and_apply(state, context, model)

        # ---- Phase 2c: Run checks --------------------------------------
        print(f"[dev_loop] Running project checks...")
        checks_passed, check_log = _run_checks(state)

        if checks_passed:
            state.status = "verifying"
            save_state(state)
            print("[dev_loop] Checks passed.")
            # All pending -> done if checks pass
            for t in state.todos:
                if t.status == "in_progress":
                    mark_todo_status(state, t.id, "done")
            break
        else:
            state.status = "fixing"
            _handle_check_failure(state, check_log, context, model)
            save_state(state)

    # ---- Phase 3: Final acceptance review ------------------------------
    state.status = "verifying"
    save_state(state)

    full_diff = _get_diff_since_base(state)
    all_checks = "\n\n".join(
        f"Loop {c.loop_index} ({'PASS' if c.passed else 'FAIL'}):\n"
        f"{Path(c.output_path).read_text(encoding='utf-8', errors='replace') if c.output_path and Path(c.output_path).is_file() else '(no log)'}"
        for c in state.check_results
    )

    print("[dev_loop] Running final acceptance review...")
    try:
        review = final_acceptance_review(state.acceptance, full_diff, all_checks, model)
        if review.get("accepted", False):
            state.status = "done"
            print("[dev_loop] ACCEPTED: All criteria met.")
        else:
            recommendation = review.get("recommendation", "manual_review")
            remaining = review.get("remaining_issues", [])
            print(f"[dev_loop] NOT ACCEPTED: recommendation={recommendation}, issues={remaining}")
            if recommendation == "retry" and state.loop_index < state.max_loops:
                state.loop_index += 1
                for issue in remaining:
                    add_bug(state, str(issue), str(issue))
                # Tail-recurse: the next iteration handles these bugs
            else:
                state.status = "failed"
    except Exception as e:
        print(f"[dev_loop] Final review failed: {e}", file=sys.stderr)
        state.status = "failed"

    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_acceptance_md(state: LoopState, artifact_dir: Path) -> None:
    """Write ``acceptance.md``."""
    lines = ["# Acceptance Criteria\n"]
    for i, a in enumerate(state.acceptance, 1):
        lines.append(f"{i}. {a}")
    lines.append("")
    (artifact_dir / "acceptance.md").write_text("\n".join(lines), encoding="utf-8")


def _write_plan_md(state: LoopState, artifact_dir: Path) -> None:
    """Write ``plan.md``."""
    (artifact_dir / "plan.md").write_text(state.plan or "# Plan\n\n(empty)\n", encoding="utf-8")


def _write_todo_md(state: LoopState, artifact_dir: Path) -> None:
    """Write ``todo.md``."""
    lines = ["# Todo List\n"]
    for t in state.todos:
        status_icon = {"pending": " ", "in_progress": "~", "done": "x", "blocked": "!"}.get(t.status, " ")
        lines.append(f"- [{status_icon}] **{t.id}**: {t.title}")
        if t.description:
            lines.append(f"  {t.description}")
        if t.files:
            lines.append(f"  Files: {', '.join(t.files)}")
    lines.append("")
    (artifact_dir / "todo.md").write_text("\n".join(lines), encoding="utf-8")


def _write_bugs_md(state: LoopState, artifact_dir: Path) -> None:
    """Write ``bugs.md``."""
    lines = ["# Open Bugs\n"]
    bugs = get_open_bugs(state)
    if not bugs:
        lines.append("No open bugs.\n")
    else:
        for b in bugs:
            lines.append(f"## {b.id}: {b.title}")
            lines.append(f"- Severity: {b.severity}")
            lines.append(f"- Status: {b.status}")
            if b.description:
                lines.append(f"- Description: {b.description}")
            if b.failure_signature:
                lines.append(f"- Signature: `{b.failure_signature}`")
            lines.append("")
    (artifact_dir / "bugs.md").write_text("\n".join(lines), encoding="utf-8")


def _implement_todos_parallel(
    state: LoopState,
    todos: list[TodoItem],
    context: str,
    model: str,
    max_parallel: int,
) -> None:
    """Call DeepSeek in parallel to implement pending todos."""
    import json as _json

    state_json = _json.dumps({
        "loop_index": state.loop_index,
        "completed_todos": [t.id for t in state.todos if t.status == "done"],
        "open_bugs": [b.id for b in get_open_bugs(state)],
    }, indent=2)

    artifact_dir = get_artifact_dir()

    def _implement_one(todo: TodoItem) -> tuple[str, str, str] | None:
        """Return (todo_id, patch_path, diff_content) or None on failure."""
        try:
            print(f"[dev_loop]   Implementing {todo.id}: {todo.title}")
            diff = implement_todo(todo, state.acceptance, context, state_json, model)
            patch_path = artifact_dir / f"patch_{todo.id}_{state.loop_index}.diff"
            patch_path.write_text(diff, encoding="utf-8")
            mark_todo_status(state, todo.id, "in_progress")
            return (todo.id, str(patch_path), diff)
        except Exception as e:
            print(f"[dev_loop]   ERROR implementing {todo.id}: {e}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=min(max_parallel, len(todos))) as executor:
        futures = {executor.submit(_implement_one, todo): todo for todo in todos}
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                todo = futures[future]
                mark_todo_status(state, todo.id, "blocked")
                add_bug(state, f"Implementation failed for {todo.id}", f"DeepSeek failed to generate patch for {todo.title}")
                _write_bugs_md(state, artifact_dir)

    save_state(state)


def _review_and_apply(state: LoopState, context: str, model: str) -> None:
    """Review candidate patches and apply those that pass review."""
    artifact_dir = get_artifact_dir()
    # Find patches from the current loop that haven't been reviewed
    current_patches = [p for p in state.patches if p.loop_index == state.loop_index]
    if not current_patches:
        return

    for todo in state.todos:
        if todo.status not in ("in_progress",):
            continue

        patch_path = artifact_dir / f"patch_{todo.id}_{state.loop_index}.diff"
        if not patch_path.is_file():
            continue

        # Check patch size
        too_large, fc, lc = is_patch_too_large(patch_path)
        if too_large:
            print(f"[dev_loop]   Patch {todo.id} too large ({fc} files, {lc} lines). Stopping todo.")
            mark_todo_status(state, todo.id, "blocked")
            add_bug(state, f"Patch too large for {todo.id}",
                    f"Patch has {fc} files and {lc} lines (limits: 8 files, 500 lines). Split this todo into smaller items.")
            _write_bugs_md(state, artifact_dir)
            continue

        patch_content = patch_path.read_text(encoding="utf-8", errors="replace")

        # Review
        print(f"[dev_loop]   Reviewing {todo.id}...")
        try:
            review = review_candidate_patch(todo, state.acceptance, patch_content, context, model)
        except Exception as e:
            print(f"[dev_loop]   Review failed for {todo.id}: {e}", file=sys.stderr)
            add_bug(state, f"Review failed for {todo.id}", str(e))
            _write_bugs_md(state, artifact_dir)
            continue

        approved = review.get("approved", False)
        safety_flags = review.get("safety_flags", [])

        if safety_flags:
            for flag in safety_flags:
                add_bug(state, f"Safety flag: {flag}", flag, severity="error")
            _write_bugs_md(state, artifact_dir)
            continue

        if not approved:
            findings = review.get("findings", [])
            for f in findings:
                if f.get("severity") == "error":
                    add_bug(state, f.get("message", str(f)), str(f), severity="error")
            _write_bugs_md(state, artifact_dir)
            print(f"[dev_loop]   Review rejected {todo.id}: {review.get('summary', '')}")
            continue

        # Validate patch safety
        ok, output = _apply_patch_check(patch_path)
        if not ok:
            print(f"[dev_loop]   Patch check failed for {todo.id}: {output[:200]}")
            add_bug(state, f"Safety check failed for {todo.id}", output)
            _write_bugs_md(state, artifact_dir)
            continue

        # Apply patch
        print(f"[dev_loop]   Applying {todo.id}...")
        ok, output = _apply_patch(patch_path)
        if ok:
            record_patch(
                state,
                patch_id=f"patch-{todo.id}-{state.loop_index}",
                path=str(patch_path),
                template="implement_todo",
                todo_id=todo.id,
                file_count=patch_content.count("--- a/"),
                line_count=len(patch_content.splitlines()),
                applied=True,
                check_result="pending",
            )
            print(f"[dev_loop]   Applied {todo.id}")
        else:
            add_bug(state, f"Apply failed for {todo.id}", output)
            _write_bugs_md(state, artifact_dir)

    save_state(state)


def _handle_check_failure(state: LoopState, check_log: str, context: str, model: str) -> None:
    """Process a check failure: record bugs and attempt fix."""
    artifact_dir = get_artifact_dir()

    # Parse check log for failure signatures
    sig = _failure_signature(message=check_log[:500])

    if has_failure_oscillation(state, sig):
        print(f"[dev_loop]   Oscillation detected (signature {sig} repeated). Stopping fix loop.")
        state.status = "failed"
        save_state(state)
        return

    record_failure(state, sig)

    # Parse common failure patterns
    for line in check_log.splitlines():
        if "FAILED" in line or "FAIL" in line:
            add_bug(state, f"Check failure: {line.strip()[:120]}", line.strip()[:500], line.strip())
        elif "error:" in line.lower() or "Error:" in line:
            add_bug(state, f"Error: {line.strip()[:120]}", line.strip()[:500], line.strip())

    _write_bugs_md(state, artifact_dir)
    save_state(state)

    open_bugs = get_open_bugs(state)
    if not open_bugs:
        print("[dev_loop]   No bugs to fix, but checks failed. Manual investigation needed.")
        return

    # Attempt fix
    print(f"[dev_loop]   Attempting fix for {len(open_bugs)} open bugs...")
    patch_history = "\n".join(
        f"- {p.id}: {'applied' if p.applied else 'not applied'} ({p.file_count} files, {p.line_count} lines)"
        for p in state.patches[-5:]  # last 5 patches
    )

    try:
        fix_diff = fix_open_bugs(open_bugs, state.acceptance, context, patch_history, model)
        fix_path = artifact_dir / f"fix_{state.loop_index}.diff"
        fix_path.write_text(fix_diff, encoding="utf-8")

        # Validate and apply fix
        ok, output = _apply_patch_check(fix_path)
        if ok:
            ok2, output2 = _apply_patch(fix_path)
            if ok2:
                record_patch(
                    state,
                    patch_id=f"fix-{state.loop_index}",
                    path=str(fix_path),
                    template="fix_open_bugs",
                    file_count=fix_diff.count("--- a/"),
                    line_count=len(fix_diff.splitlines()),
                    applied=True,
                    check_result="pending",
                )
                for bug in open_bugs:
                    close_bug(state, bug.id)
                print(f"[dev_loop]   Fix applied for loop {state.loop_index}")
            else:
                add_bug(state, f"Fix apply failed (loop {state.loop_index})", output2)
        else:
            add_bug(state, f"Fix validation failed (loop {state.loop_index})", output)
            _write_bugs_md(state, artifact_dir)
    except Exception as e:
        print(f"[dev_loop]   Fix generation failed: {e}", file=sys.stderr)
        add_bug(state, f"Fix generation failed (loop {state.loop_index})", str(e))

    _write_bugs_md(state, artifact_dir)
    save_state(state)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Forward development loop orchestrator for deepseek-forge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--task", required=True, help="Path to the task description file.")
    parser.add_argument("--model", default=None, help="DeepSeek model name (default: $DEEPSEEK_MODEL or deepseek-v4-pro).")
    parser.add_argument("--context", default=None, help="Path to pre-collected context file (optional).")
    parser.add_argument("--output-dir", default=None, help="Override artifact directory base.")
    parser.add_argument("--max-loops", type=int, default=None, help="Max loop iterations (default: $DEEPSEEK_FORGE_MAX_LOOPS or 5).")
    parser.add_argument("--resume", action="store_true", help="Resume from existing state.json if found.")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    task_path = Path(args.task)
    if not task_path.is_file():
        print(f"Error: task file not found: {task_path}", file=sys.stderr)
        sys.exit(1)
    task = task_path.read_text(encoding="utf-8", errors="replace")

    context = ""
    if args.context:
        ctx_path = Path(args.context)
        if ctx_path.is_file():
            context = ctx_path.read_text(encoding="utf-8", errors="replace")

    if args.output_dir:
        os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = args.output_dir

    model = args.model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")

    if args.max_loops is not None:
        os.environ["DEEPSEEK_FORGE_MAX_LOOPS"] = str(args.max_loops)

    state = None
    if args.resume:
        state = load_state()
        if state is not None:
            print(f"[dev_loop] Resuming from state: run={state.run_id}, loop={state.loop_index}")

    result = run_forward_development_loop(task, context, model, state)

    print(f"\n[dev_loop] Final status: {result.status}")
    print(f"[dev_loop] Todos: {sum(1 for t in result.todos if t.status == 'done')}/{len(result.todos)} done")
    print(f"[dev_loop] Open bugs: {len(get_open_bugs(result))}")
    print(f"[dev_loop] Artifacts: {get_artifact_dir()}")


if __name__ == "__main__":
    main()
