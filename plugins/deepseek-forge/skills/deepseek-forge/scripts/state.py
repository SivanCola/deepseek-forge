#!/usr/bin/env python3
"""State management for deepseek-forge forward development loops.

Reads and writes ``state.json`` — the machine-readable source of truth for
loop progress, open bugs, patch history, and anti-oscillation tracking.

Default location is the isolated artifact directory
(``{artifact_dir}/state.json``).  When ``DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS``
is ``"true"``, the state lands in ``.deepseek-forge/state.json``.

Uses only stdlib — no external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from forge_config import get_artifact_dir


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TodoItem:
    id: str
    title: str
    description: str = ""
    files: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | in_progress | done | blocked


@dataclass
class BugItem:
    id: str
    title: str
    description: str = ""
    failure_signature: str = ""
    severity: str = "error"  # error | warning
    status: str = "open"  # open | fixed | wont_fix
    source_loop: int = 0


@dataclass
class PatchRecord:
    id: str
    path: str
    template: str  # implement_todo | fix_open_bugs | write_tests_for_todo
    todo_id: str = ""
    loop_index: int = 0
    file_count: int = 0
    line_count: int = 0
    applied: bool = False
    check_result: str = ""  # passed | failed | not_run


@dataclass
class CheckResult:
    loop_index: int
    command: str
    exit_code: int
    output_path: str = ""
    passed: bool = False


@dataclass
class LoopState:
    run_id: str
    thread_id: str
    repo_root: str
    base_sha: str
    loop_index: int = 0
    todos: list[TodoItem] = field(default_factory=list)
    open_bugs: list[BugItem] = field(default_factory=list)
    patches: list[PatchRecord] = field(default_factory=list)
    check_results: list[CheckResult] = field(default_factory=list)
    failure_signatures: list[str] = field(default_factory=list)
    status: str = "initialized"  # initialized | planning | implementing | reviewing | fixing | verifying | done | failed
    acceptance: list[str] = field(default_factory=list)
    plan: str = ""
    max_loops: int = 5
    max_parallel_agents: int = 3


# ---------------------------------------------------------------------------
# State path resolution
# ---------------------------------------------------------------------------


def _get_state_path() -> Path:
    """Return the path to ``state.json`` in the artifact directory."""
    return get_artifact_dir() / "state.json"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _get_repo_root() -> str:
    """Return the absolute path of the git repository root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return str(Path.cwd().resolve())


def _get_base_sha() -> str:
    """Return the current HEAD SHA, or empty string."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def create_state(
    *,
    run_id: str = "",
    thread_id: str = "",
    acceptance: list[str] | None = None,
    plan: str = "",
    todos: list[TodoItem] | None = None,
) -> LoopState:
    """Create a fresh ``LoopState`` with the current repo/environment metadata."""
    from forge_config import _run_id, _thread_id, get_max_loops, get_max_parallel_agents

    return LoopState(
        run_id=run_id or _run_id(),
        thread_id=thread_id or _thread_id(),
        repo_root=_get_repo_root(),
        base_sha=_get_base_sha(),
        acceptance=acceptance or [],
        plan=plan,
        todos=todos or [],
        max_loops=get_max_loops(),
        max_parallel_agents=get_max_parallel_agents(),
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _state_to_dict(state: LoopState) -> dict[str, Any]:
    """Convert a ``LoopState`` to a JSON-serialisable dict."""
    d: dict[str, Any] = {
        "run_id": state.run_id,
        "thread_id": state.thread_id,
        "repo_root": state.repo_root,
        "base_sha": state.base_sha,
        "loop_index": state.loop_index,
        "status": state.status,
        "max_loops": state.max_loops,
        "max_parallel_agents": state.max_parallel_agents,
        "acceptance": state.acceptance,
        "plan": state.plan,
        "todos": [asdict(t) for t in state.todos],
        "open_bugs": [asdict(b) for b in state.open_bugs],
        "patches": [asdict(p) for p in state.patches],
        "check_results": [asdict(c) for c in state.check_results],
        "failure_signatures": state.failure_signatures,
    }
    return d


def _dict_to_state(d: dict[str, Any]) -> LoopState:
    """Convert a JSON-loaded dict back to a ``LoopState``."""
    todos = [TodoItem(**t) for t in d.get("todos", [])]
    open_bugs = [BugItem(**b) for b in d.get("open_bugs", [])]
    patches = [PatchRecord(**p) for p in d.get("patches", [])]
    check_results = [CheckResult(**c) for c in d.get("check_results", [])]

    return LoopState(
        run_id=d.get("run_id", ""),
        thread_id=d.get("thread_id", ""),
        repo_root=d.get("repo_root", ""),
        base_sha=d.get("base_sha", ""),
        loop_index=d.get("loop_index", 0),
        todos=todos,
        open_bugs=open_bugs,
        patches=patches,
        check_results=check_results,
        failure_signatures=d.get("failure_signatures", []),
        status=d.get("status", "initialized"),
        acceptance=d.get("acceptance", []),
        plan=d.get("plan", ""),
        max_loops=d.get("max_loops", 5),
        max_parallel_agents=d.get("max_parallel_agents", 3),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_state(state: LoopState, path: Path | None = None) -> Path:
    """Write *state* to ``state.json``.  Returns the path written."""
    filepath = path or _get_state_path()
    filepath.parent.mkdir(parents=True, exist_ok=True)
    data = _state_to_dict(state)
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return filepath


def load_state(path: Path | None = None) -> LoopState | None:
    """Read and parse ``state.json``, returning ``None`` if not found."""
    filepath = path or _get_state_path()
    if not filepath.is_file():
        return None
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return _dict_to_state(data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Anti-oscillation helpers
# ---------------------------------------------------------------------------


def _failure_signature(bug: BugItem | None = None, check: CheckResult | None = None,
                       message: str = "") -> str:
    """Compute a stable hash for a failure so identical failures are recognised."""
    parts: list[str] = []
    if bug:
        parts.extend([bug.title, bug.description, bug.failure_signature])
    if check:
        parts.extend([check.command, str(check.exit_code)])
    if message:
        parts.append(message)
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def has_failure_oscillation(state: LoopState, signature: str) -> bool:
    """Return ``True`` if *signature* has appeared at least twice before."""
    count = sum(1 for s in state.failure_signatures if s == signature)
    return count >= 2


def record_failure(state: LoopState, signature: str) -> None:
    """Append a failure signature to the state for oscillation tracking."""
    state.failure_signatures.append(signature)


def is_patch_too_large(patch_path: str | Path,
                       max_files: int = 8,
                       max_lines: int = 500) -> tuple[bool, int, int]:
    """Return ``(too_large, file_count, line_count)`` for the given patch."""
    p = Path(patch_path)
    if not p.is_file():
        return False, 0, 0
    content = p.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    line_count = len(lines)
    file_count = 0
    for line in lines:
        if line.startswith("--- a/") or line.startswith("--- /dev/null"):
            file_count += 1
    too_large = file_count > max_files or line_count > max_lines
    return too_large, file_count, line_count


def should_split_todo(state: LoopState, patch_path: str | Path) -> bool:
    """Return ``True`` if the patch exceeds size limits and the todo should be split."""
    too_large, fc, lc = is_patch_too_large(patch_path)
    return too_large


def should_stop_loop(state: LoopState) -> tuple[bool, str]:
    """Return ``(should_stop, reason)`` based on loop limits and oscillation.

    Checks:
    1. Loop index exceeds max_loops
    2. Same failure signature repeated >= 2 times
    """
    if state.loop_index >= state.max_loops:
        return True, f"Max loops ({state.max_loops}) reached"

    return False, ""


# ---------------------------------------------------------------------------
# Bug management
# ---------------------------------------------------------------------------


def add_bug(state: LoopState, title: str, description: str = "",
            failure_message: str = "", severity: str = "error") -> BugItem:
    """Add a new open bug to the state and return it."""
    sig = _failure_signature(message=failure_message or description)
    bug = BugItem(
        id=f"bug-{len(state.open_bugs) + 1}",
        title=title,
        description=description,
        failure_signature=sig,
        severity=severity,
        status="open",
        source_loop=state.loop_index,
    )
    state.open_bugs.append(bug)
    return bug


def close_bug(state: LoopState, bug_id: str) -> bool:
    """Mark a bug as fixed.  Returns ``True`` if found and updated."""
    for bug in state.open_bugs:
        if bug.id == bug_id and bug.status == "open":
            bug.status = "fixed"
            return True
    return False


def get_open_bugs(state: LoopState) -> list[BugItem]:
    """Return all currently open bugs."""
    return [b for b in state.open_bugs if b.status == "open"]


# ---------------------------------------------------------------------------
# Todo management
# ---------------------------------------------------------------------------


def get_pending_todos(state: LoopState) -> list[TodoItem]:
    """Return todos that are still pending."""
    return [t for t in state.todos if t.status == "pending"]


def get_in_progress_todos(state: LoopState) -> list[TodoItem]:
    """Return todos currently being worked on."""
    return [t for t in state.todos if t.status == "in_progress"]


def mark_todo_status(state: LoopState, todo_id: str, status: str) -> bool:
    """Update a todo's status.  Returns ``True`` if found."""
    for todo in state.todos:
        if todo.id == todo_id:
            todo.status = status
            return True
    return False


def all_todos_done(state: LoopState) -> bool:
    """Return ``True`` if all todos are ``done``."""
    return all(t.status == "done" for t in state.todos) and len(state.todos) > 0


# ---------------------------------------------------------------------------
# Patch management
# ---------------------------------------------------------------------------


def record_patch(state: LoopState, patch_id: str, path: str, template: str,
                 todo_id: str = "", file_count: int = 0, line_count: int = 0,
                 applied: bool = False, check_result: str = "not_run") -> PatchRecord:
    """Add a patch record to the state."""
    rec = PatchRecord(
        id=patch_id,
        path=path,
        template=template,
        todo_id=todo_id,
        loop_index=state.loop_index,
        file_count=file_count,
        line_count=line_count,
        applied=applied,
        check_result=check_result,
    )
    state.patches.append(rec)
    return rec
