"""Unit tests for scripts/state.py — state.json management and anti-oscillation.

Uses only stdlib (unittest, tempfile, pathlib).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "plugins"
    / "deepseek-forge"
    / "skills"
    / "deepseek-forge"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_PATH))

import state as st
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


class TestCreateState(unittest.TestCase):
    """Tests for :func:`create_state`."""

    def test_creates_with_defaults(self):
        s = create_state()
        self.assertIsInstance(s, LoopState)
        self.assertTrue(s.run_id)
        self.assertTrue(s.thread_id)
        self.assertTrue(s.repo_root)
        self.assertEqual(s.loop_index, 0)
        self.assertEqual(s.status, "initialized")
        self.assertEqual(s.todos, [])
        self.assertEqual(s.open_bugs, [])
        self.assertEqual(s.failure_signatures, [])
        self.assertGreater(s.max_loops, 0)

    def test_creates_with_custom_values(self):
        s = create_state(
            run_id="test-run-1",
            thread_id="thread-42",
            acceptance=["criteria 1", "criteria 2"],
            plan="# My Plan",
            todos=[TodoItem(id="todo-1", title="First todo", description="Do it", files=["a.py"])],
        )
        self.assertEqual(s.run_id, "test-run-1")
        self.assertEqual(s.thread_id, "thread-42")
        self.assertEqual(s.acceptance, ["criteria 1", "criteria 2"])
        self.assertEqual(s.plan, "# My Plan")
        self.assertEqual(len(s.todos), 1)


class TestStateSerialisation(unittest.TestCase):
    """Tests for save/load round-trip."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._saved_artifact_dir = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
        os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = self._tmpdir.name
        self._saved_thread = os.environ.get("CODEX_THREAD_ID")
        os.environ["CODEX_THREAD_ID"] = "test-thread"
        self._saved_run = os.environ.get("DEEPSEEK_FORGE_RUN_ID")
        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "test-run"

    def tearDown(self):
        self._tmpdir.cleanup()
        for key, val in [
            ("DEEPSEEK_FORGE_ARTIFACT_DIR", self._saved_artifact_dir),
            ("CODEX_THREAD_ID", self._saved_thread),
            ("DEEPSEEK_FORGE_RUN_ID", self._saved_run),
        ]:
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)

    def test_round_trip_minimal(self):
        s = create_state(
            acceptance=["Works"],
            todos=[TodoItem(id="todo-1", title="T1", description="D1", files=["f.py"])],
        )
        path = save_state(s)
        self.assertTrue(path.is_file())

        loaded = load_state(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.run_id, s.run_id)
        self.assertEqual(loaded.thread_id, s.thread_id)
        self.assertEqual(loaded.acceptance, s.acceptance)
        self.assertEqual(len(loaded.todos), 1)
        self.assertEqual(loaded.todos[0].id, "todo-1")

    def test_round_trip_with_bugs(self):
        s = create_state()
        add_bug(s, "Test failure", "Something broke", "FAILED: test_x")
        add_bug(s, "Type error", "Wrong type", "error: type mismatch")
        s.loop_index = 2
        path = save_state(s)

        loaded = load_state(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.loop_index, 2)
        self.assertEqual(len(loaded.open_bugs), 2)
        self.assertEqual(loaded.open_bugs[0].title, "Test failure")
        self.assertEqual(loaded.open_bugs[1].title, "Type error")

    def test_round_trip_with_patches_and_checks(self):
        s = create_state()
        s.loop_index = 1
        record_patch(s, "p1", "/tmp/p.diff", "implement_todo", "todo-1", 3, 50, True, "passed")
        s.check_results.append(CheckResult(loop_index=1, command="bash check.sh", exit_code=0, output_path="/tmp/check.log", passed=True))
        path = save_state(s)

        loaded = load_state(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.patches), 1)
        self.assertEqual(loaded.patches[0].id, "p1")
        self.assertTrue(loaded.patches[0].applied)
        self.assertEqual(len(loaded.check_results), 1)
        self.assertTrue(loaded.check_results[0].passed)

    def test_load_nonexistent(self):
        result = load_state(Path("/nonexistent/state.json"))
        self.assertIsNone(result)


class TestTodoManagement(unittest.TestCase):
    """Tests for todo state management."""

    def setUp(self):
        self.state = create_state(
            todos=[
                TodoItem(id="todo-1", title="First", description="A"),
                TodoItem(id="todo-2", title="Second", description="B"),
                TodoItem(id="todo-3", title="Third", description="C"),
            ]
        )

    def test_get_pending_todos(self):
        pending = get_pending_todos(self.state)
        self.assertEqual(len(pending), 3)

    def test_mark_todo_status(self):
        self.assertTrue(mark_todo_status(self.state, "todo-1", "in_progress"))
        self.assertTrue(mark_todo_status(self.state, "todo-2", "done"))
        pending = get_pending_todos(self.state)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, "todo-3")

    def test_mark_nonexistent_todo(self):
        self.assertFalse(mark_todo_status(self.state, "nonexistent", "done"))

    def test_all_todos_done(self):
        self.assertFalse(all_todos_done(self.state))
        for tid in ["todo-1", "todo-2", "todo-3"]:
            mark_todo_status(self.state, tid, "done")
        self.assertTrue(all_todos_done(self.state))

    def test_all_todos_done_empty(self):
        s = create_state()
        self.assertFalse(all_todos_done(s))


class TestBugManagement(unittest.TestCase):
    """Tests for bug state management."""

    def setUp(self):
        self.state = create_state()

    def test_add_bug(self):
        bug = add_bug(self.state, "Test failed", "assertion error", "FAILED: test_x")
        self.assertEqual(bug.id, "bug-1")
        self.assertEqual(bug.status, "open")
        self.assertEqual(bug.severity, "error")
        self.assertEqual(bug.source_loop, 0)
        self.assertTrue(bug.failure_signature)

    def test_add_bug_increments_id(self):
        add_bug(self.state, "Bug 1")
        add_bug(self.state, "Bug 2")
        add_bug(self.state, "Bug 3")
        self.assertEqual(len(self.state.open_bugs), 3)
        self.assertEqual(self.state.open_bugs[0].id, "bug-1")
        self.assertEqual(self.state.open_bugs[2].id, "bug-3")

    def test_close_bug(self):
        add_bug(self.state, "Bug 1")
        add_bug(self.state, "Bug 2")
        self.assertTrue(close_bug(self.state, "bug-1"))
        self.assertFalse(close_bug(self.state, "bug-1"))  # already closed
        self.assertFalse(close_bug(self.state, "nonexistent"))

    def test_get_open_bugs(self):
        add_bug(self.state, "Bug 1")
        add_bug(self.state, "Bug 2")
        close_bug(self.state, "bug-1")
        open_bugs = get_open_bugs(self.state)
        self.assertEqual(len(open_bugs), 1)
        self.assertEqual(open_bugs[0].id, "bug-2")

    def test_add_bug_with_severity(self):
        bug = add_bug(self.state, "Warning issue", "minor", severity="warning")
        self.assertEqual(bug.severity, "warning")


class TestFailureSignatures(unittest.TestCase):
    """Tests for failure signature computation and oscillation detection."""

    def setUp(self):
        self.state = create_state()

    def test_same_message_produces_same_signature(self):
        sig1 = _failure_signature(message="FAILED: test_x")
        sig2 = _failure_signature(message="FAILED: test_x")
        self.assertEqual(sig1, sig2)

    def test_different_messages_produce_different_signatures(self):
        sig1 = _failure_signature(message="FAILED: test_x")
        sig2 = _failure_signature(message="FAILED: test_y")
        self.assertNotEqual(sig1, sig2)

    def test_has_failure_oscillation(self):
        sig = _failure_signature(message="FAILED: test_x")
        self.assertFalse(has_failure_oscillation(self.state, sig))
        record_failure(self.state, sig)
        self.assertFalse(has_failure_oscillation(self.state, sig))
        record_failure(self.state, sig)
        self.assertTrue(has_failure_oscillation(self.state, sig))

    def test_record_failure(self):
        sig = _failure_signature(message="test failure")
        record_failure(self.state, sig)
        self.assertEqual(len(self.state.failure_signatures), 1)
        self.assertEqual(self.state.failure_signatures[0], sig)


class TestAntiOscillation(unittest.TestCase):
    """Tests for loop stopping conditions."""

    def test_should_stop_max_loops(self):
        s = create_state()
        s.loop_index = 5  # equal to max_loops
        should, reason = should_stop_loop(s)
        self.assertTrue(should)
        self.assertIn("Max loops", reason)

    def test_should_not_stop_under_limit(self):
        s = create_state()
        s.loop_index = 3
        should, reason = should_stop_loop(s)
        self.assertFalse(should)

    def test_custom_max_loops(self):
        s = create_state()
        s.max_loops = 2
        s.loop_index = 2
        should, reason = should_stop_loop(s)
        self.assertTrue(should)


class TestPatchSizeLimits(unittest.TestCase):
    """Tests for patch size checking."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_patch(self, files: int, lines_per_file: int) -> Path:
        """Write a patch with *files* files, each with *lines_per_file* lines."""
        parts = []
        for i in range(files):
            parts.append(f"--- a/file_{i}.py")
            parts.append(f"+++ b/file_{i}.py")
            parts.append(f"@@ -1,1 +1,1 @@")
            for j in range(lines_per_file):
                parts.append(f"+line_{j}")
        p = Path(self._tmpdir.name) / "test.diff"
        p.write_text("\n".join(parts))
        return p

    def test_small_patch_ok(self):
        p = self._write_patch(2, 10)
        too_large, fc, lc = is_patch_too_large(p)
        self.assertFalse(too_large)
        self.assertEqual(fc, 2)

    def test_too_many_files(self):
        p = self._write_patch(10, 5)
        too_large, fc, lc = is_patch_too_large(p)
        self.assertTrue(too_large)
        self.assertEqual(fc, 10)

    def test_too_many_lines(self):
        p = self._write_patch(2, 300)
        too_large, fc, lc = is_patch_too_large(p)
        self.assertTrue(too_large)
        self.assertGreaterEqual(lc, 500)

    def test_nonexistent_file(self):
        p = Path(self._tmpdir.name) / "nonexistent.diff"
        too_large, fc, lc = is_patch_too_large(p)
        self.assertFalse(too_large)
        self.assertEqual(fc, 0)

    def test_custom_limits(self):
        p = self._write_patch(3, 20)
        too_large, fc, lc = is_patch_too_large(p, max_files=2, max_lines=100)
        self.assertTrue(too_large)

    def test_should_split_todo(self):
        state = create_state()
        p = self._write_patch(10, 5)
        self.assertTrue(should_split_todo(state, p))

    def test_should_not_split_small_patch(self):
        state = create_state()
        p = self._write_patch(2, 10)
        self.assertFalse(should_split_todo(state, p))


if __name__ == "__main__":
    unittest.main()
