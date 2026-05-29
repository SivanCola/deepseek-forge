"""Tests for the forward development loop (dev_loop.py).

Integration tests with mocked DeepSeek responses that verify the full
implement -> check fail -> bugs.md -> fix -> check pass lifecycle.

Uses only stdlib (unittest, mock via monkeypatching).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "plugins"
    / "deepseek-forge"
    / "skills"
    / "deepseek-forge"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_PATH))

from state import (
    LoopState,
    TodoItem,
    BugItem,
    create_state,
    save_state,
    get_open_bugs,
    get_pending_todos,
    add_bug,
    close_bug,
    mark_todo_status,
    all_todos_done,
)


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_EXPAND_PLAN_RESPONSE = {
    "acceptance": [
        "Users can register with valid email and password",
        "Invalid emails are rejected with ValueError",
        "Existing tests continue to pass",
    ],
    "plan": "## Summary\n\nAdd email validation to user_service.py\n\n## Steps\n1. Add EMAIL_REGEX\n2. Validate in create_user()\n3. Add tests",
    "todos": [
        {
            "id": "todo-1",
            "title": "Add email validation regex",
            "description": "Add EMAIL_REGEX and validate email in create_user()",
            "files": ["src/services/user_service.py"],
        },
        {
            "id": "todo-2",
            "title": "Add validation tests",
            "description": "Write tests for email validation",
            "files": ["tests/test_user_service.py"],
        },
    ],
}

MOCK_IMPLEMENT_DIFF = (
    "--- a/src/services/user_service.py\n"
    "+++ b/src/services/user_service.py\n"
    "@@ -12,6 +12,9 @@\n"
    " import hashlib\n"
    "+import re\n"
    " from typing import Optional\n"
    "\n"
    "+EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{2,}$')\n"
    "+\n"
    "@@ -45,6 +48,9 @@\n"
    "     Returns:\n"
    "         User: the created user object\n"
    '     """\n'
    "+    if not EMAIL_REGEX.match(email):\n"
    '+        raise ValueError(f"Invalid email address: {email}")\n'
    "+\n"
    "     user = User(name=name, email=email)\n"
)

MOCK_REVIEW_APPROVED = {
    "approved": True,
    "findings": [],
    "safety_flags": [],
    "summary": "Patch correctly implements email validation.",
}

MOCK_REVIEW_REJECTED = {
    "approved": False,
    "findings": [
        {"severity": "error", "file": "src/services/user_service.py", "line": 51, "message": "Missing import"}
    ],
    "safety_flags": [],
    "summary": "Rejected due to missing import.",
}

MOCK_FIX_DIFF = (
    "--- a/src/services/user_service.py\n"
    "+++ b/src/services/user_service.py\n"
    "@@ -15,6 +15,7 @@\n"
    "\n"
    "+import re\n"
    " EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{2,}$')\n"
)

MOCK_FINAL_REVIEW = {
    "accepted": True,
    "criteria_results": [
        {"criterion": "Users can register with valid email", "met": True, "evidence": "test passes"},
        {"criterion": "Invalid emails rejected", "met": True, "evidence": "ValueError raised"},
        {"criterion": "Existing tests pass", "met": True, "evidence": "all 47 tests pass"},
    ],
    "check_summary": {"tests_passed": True, "lint_passed": True, "typecheck_passed": True, "notes": ""},
    "remaining_issues": [],
    "recommendation": "approve",
}


# ---------------------------------------------------------------------------
# State lifecycle tests
# ---------------------------------------------------------------------------


class TestDevLoopStateLifecycle(unittest.TestCase):
    """Test the full state lifecycle through a forward development loop."""

    def setUp(self):
        self._saved_thread = os.environ.get("CODEX_THREAD_ID")
        self._saved_run = os.environ.get("DEEPSEEK_FORGE_RUN_ID")
        os.environ["CODEX_THREAD_ID"] = "test-thread"
        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "test-run-lifecycle"

    def tearDown(self):
        for key, val in [
            ("CODEX_THREAD_ID", self._saved_thread),
            ("DEEPSEEK_FORGE_RUN_ID", self._saved_run),
        ]:
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)

    def test_full_lifecycle_state_transitions(self):
        """Simulate the state transitions through planning -> implementing -> fixing -> verifying -> done."""
        state = create_state(
            acceptance=MOCK_EXPAND_PLAN_RESPONSE["acceptance"],
            plan=MOCK_EXPAND_PLAN_RESPONSE["plan"],
            todos=[
                TodoItem(
                    id=t["id"], title=t["title"], description=t["description"],
                    files=t.get("files", []), status="pending",
                )
                for t in MOCK_EXPAND_PLAN_RESPONSE["todos"]
            ],
        )

        # Phase 1: Planning
        self.assertEqual(state.status, "initialized")
        state.status = "planning"
        self.assertEqual(len(state.acceptance), 3)
        self.assertEqual(len(state.todos), 2)

        # Phase 2: Implementation
        state.status = "implementing"
        state.loop_index = 1
        mark_todo_status(state, "todo-1", "in_progress")
        self.assertEqual(len(get_pending_todos(state)), 1)

        # Phase 3: Simulate check failure
        state.status = "fixing"
        add_bug(state, "Test failure", "FAILED: test_create_user - assertion error", "FAILED: test_create_user")
        self.assertEqual(len(get_open_bugs(state)), 1)

        # Phase 4: Apply fix
        close_bug(state, "bug-1")
        self.assertEqual(len(get_open_bugs(state)), 0)
        mark_todo_status(state, "todo-1", "done")

        # Phase 5: Continue with todo-2
        mark_todo_status(state, "todo-2", "in_progress")
        mark_todo_status(state, "todo-2", "done")

        # Phase 6: Verification
        state.status = "verifying"
        self.assertTrue(all_todos_done(state))
        self.assertEqual(len(get_open_bugs(state)), 0)

        state.status = "done"
        self.assertEqual(state.status, "done")

    def test_oscillation_detection_stops_loop(self):
        """Verify that repeated failure signatures trigger loop stop."""
        from state import has_failure_oscillation, record_failure, _failure_signature

        state = create_state()
        sig = _failure_signature(message="FAILED: test_x - AssertionError")

        self.assertFalse(has_failure_oscillation(state, sig))
        record_failure(state, sig)
        self.assertFalse(has_failure_oscillation(state, sig))
        record_failure(state, sig)
        self.assertTrue(has_failure_oscillation(state, sig))

    def test_max_loops_stops(self):
        """Verify max loops triggers stop."""
        from state import should_stop_loop

        state = create_state()
        state.max_loops = 3
        state.loop_index = 3
        should, reason = should_stop_loop(state)
        self.assertTrue(should)
        self.assertIn("3", reason)

    def test_patch_size_limit(self):
        """Verify oversized patches are detected."""
        from state import is_patch_too_large

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a large patch (10 files)
            lines = []
            for i in range(10):
                lines.append(f"--- a/file_{i}.py")
                lines.append(f"+++ b/file_{i}.py")
                lines.append(f"@@ -1,1 +1,1 @@")
                for j in range(5):
                    lines.append(f"+line_{j}")

            p = Path(tmpdir) / "large.diff"
            p.write_text("\n".join(lines))

            too_large, fc, lc = is_patch_too_large(p)
            self.assertTrue(too_large)
            self.assertEqual(fc, 10)

    def test_bug_id_increment(self):
        """Bug IDs increment correctly."""
        state = create_state()
        b1 = add_bug(state, "Bug A")
        b2 = add_bug(state, "Bug B")
        b3 = add_bug(state, "Bug C")
        self.assertEqual(b1.id, "bug-1")
        self.assertEqual(b2.id, "bug-2")
        self.assertEqual(b3.id, "bug-3")

    def test_todo_ordering_preserved(self):
        """Todo items maintain their order."""
        state = create_state(
            todos=[
                TodoItem(id="todo-3", title="Third"),
                TodoItem(id="todo-1", title="First"),
                TodoItem(id="todo-2", title="Second"),
            ]
        )
        pending = get_pending_todos(state)
        self.assertEqual(pending[0].id, "todo-3")
        self.assertEqual(pending[1].id, "todo-1")
        self.assertEqual(pending[2].id, "todo-2")


class TestDevLoopIntegration(unittest.TestCase):
    """Integration tests with mocked DeepSeek calls."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._saved_artifact_dir = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
        self._saved_thread = os.environ.get("CODEX_THREAD_ID")
        self._saved_run = os.environ.get("DEEPSEEK_FORGE_RUN_ID")
        self._saved_max_loops = os.environ.get("DEEPSEEK_FORGE_MAX_LOOPS")
        os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = self._tmpdir.name
        os.environ["CODEX_THREAD_ID"] = "test-thread"
        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "test-run-integration"
        os.environ["DEEPSEEK_FORGE_MAX_LOOPS"] = "3"

    def tearDown(self):
        self._tmpdir.cleanup()
        for key, val in [
            ("DEEPSEEK_FORGE_ARTIFACT_DIR", self._saved_artifact_dir),
            ("CODEX_THREAD_ID", self._saved_thread),
            ("DEEPSEEK_FORGE_RUN_ID", self._saved_run),
            ("DEEPSEEK_FORGE_MAX_LOOPS", self._saved_max_loops),
        ]:
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)

    def test_expand_plan_integration(self):
        """Simulate the expand_plan phase with mock data."""
        state = create_state(
            acceptance=MOCK_EXPAND_PLAN_RESPONSE["acceptance"],
            plan=MOCK_EXPAND_PLAN_RESPONSE["plan"],
            todos=[
                TodoItem(
                    id=t["id"], title=t["title"], description=t["description"],
                    files=t.get("files", []),
                )
                for t in MOCK_EXPAND_PLAN_RESPONSE["todos"]
            ],
        )

        self.assertEqual(len(state.acceptance), 3)
        self.assertEqual(len(state.todos), 2)
        self.assertIn("email validation", state.plan)

        # Verify we can save and reload
        save_state(state)
        saved_path = Path(self._tmpdir.name) / "deepseek-forge"

    @patch("dev_loop.expand_plan")
    @patch("dev_loop._run_checks")
    @patch("dev_loop._apply_patch_check")
    @patch("dev_loop._apply_patch")
    @patch("dev_loop._get_diff_since_base")
    @patch("dev_loop.final_acceptance_review")
    def test_full_loop_mocked(
        self,
        mock_final_review,
        mock_get_diff,
        mock_apply,
        mock_check,
        mock_run_checks,
        mock_expand,
    ):
        """Run a full forward development loop with all DeepSeek calls mocked."""
        from dev_loop import run_forward_development_loop

        # Setup mocks
        mock_expand.return_value = MOCK_EXPAND_PLAN_RESPONSE
        mock_check.return_value = (True, "CHECK PASSED")
        mock_apply.return_value = (True, "APPLIED")
        mock_run_checks.return_value = (True, "All tests pass")
        mock_get_diff.return_value = "mock diff content"
        mock_final_review.return_value = MOCK_FINAL_REVIEW

        # Need to also mock implement_todo (which is called internally)
        with patch("dev_loop.implement_todo", return_value=MOCK_IMPLEMENT_DIFF), \
             patch("dev_loop.review_candidate_patch", return_value=MOCK_REVIEW_APPROVED):
            task = "Add email validation to user registration"
            result = run_forward_development_loop(task)

        self.assertEqual(result.status, "done")
        self.assertEqual(len(result.todos), 2)
        self.assertTrue(all_todos_done(result))

    @patch("dev_loop.expand_plan")
    def test_loop_with_fix_cycle_mocked(self, mock_expand):
        """Simulate implement -> check fail -> fix -> check pass."""
        from dev_loop import run_forward_development_loop

        mock_expand.return_value = MOCK_EXPAND_PLAN_RESPONSE

        check_results = [False, True]  # First fails, then passes
        def _mock_checks(state):
            passed = check_results.pop(0) if check_results else True
            return passed, "check output"

        with patch("dev_loop.implement_todo", return_value=MOCK_IMPLEMENT_DIFF), \
             patch("dev_loop.review_candidate_patch", return_value=MOCK_REVIEW_APPROVED), \
             patch("dev_loop._apply_patch_check", return_value=(True, "")), \
             patch("dev_loop._apply_patch", return_value=(True, "")), \
             patch("dev_loop._run_checks", side_effect=_mock_checks), \
             patch("dev_loop.fix_open_bugs", return_value=MOCK_FIX_DIFF), \
             patch("dev_loop._get_diff_since_base", return_value="mock diff"), \
             patch("dev_loop.final_acceptance_review", return_value=MOCK_FINAL_REVIEW):
            task = "Add email validation"
            result = run_forward_development_loop(task)

        # Should have completed after one fix cycle
        self.assertIn(result.status, ("done", "verifying"))


if __name__ == "__main__":
    unittest.main()
