"""Comprehensive unit tests for scripts/task_classifier.py.

Covers all four task categories, edge cases, mixed signals, case insensitivity,
bilingual (Chinese/English) input, and garbage/noise rejection.

Uses only stdlib (unittest).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Ensure the scripts directory is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import task_classifier as tc


# ============================================================================
# Patch task classification
# ============================================================================


class TestPatchTaskClassification(unittest.TestCase):
    """Verify that code-implementation task descriptions are classified as patch_task."""

    def test_implement_feature(self):
        self.assertEqual(tc.classify_task("implement a new login feature"), "patch_task")

    def test_fix_bug(self):
        self.assertEqual(
            tc.classify_task("fix the bug in authentication module"),
            "patch_task",
        )

    def test_refactor_code(self):
        self.assertEqual(tc.classify_task("refactor the database layer"), "patch_task")

    def test_add_feature(self):
        self.assertEqual(
            tc.classify_task("add feature to support dark mode"),
            "patch_task",
        )

    def test_optimize_performance(self):
        self.assertEqual(
            tc.classify_task("optimize the search query performance"),
            "patch_task",
        )

    def test_write_unit_tests(self):
        self.assertEqual(
            tc.classify_task("write test for the user service module"),
            "patch_task",
        )

    def test_build_new_endpoint(self):
        self.assertEqual(
            tc.classify_task("build a new REST API endpoint for user profiles"),
            "patch_task",
        )


# ============================================================================
# Patch review task classification
# ============================================================================


class TestPatchReviewTaskClassification(unittest.TestCase):
    """Verify that patch-review task descriptions are classified correctly."""

    def test_review_patch(self):
        self.assertEqual(
            tc.classify_task("review this patch for security issues"),
            "patch_review_task",
        )

    def test_code_review(self):
        self.assertEqual(
            tc.classify_task("code review the changes in PR #42"),
            "patch_review_task",
        )

    def test_audit_changes(self):
        self.assertEqual(
            tc.classify_task("audit the latest commit for vulnerabilities"),
            "patch_review_task",
        )

    def test_inspect_changes(self):
        self.assertEqual(
            tc.classify_task("inspect changes to the authentication module"),
            "patch_review_task",
        )

    def test_check_patch(self):
        self.assertEqual(
            tc.classify_task("check this patch before merging to main"),
            "patch_review_task",
        )


# ============================================================================
# PR branch topology task classification
# ============================================================================


class TestPRBranchTopologyTaskClassification(unittest.TestCase):
    """Verify that PR branch governance tasks are classified correctly."""

    def test_force_push(self):
        self.assertEqual(
            tc.classify_task("PR head needs force push after rebase"),
            "pr_branch_topology_task",
        )

    def test_multiple_prs_share_head(self):
        self.assertEqual(
            tc.classify_task("multiple PRs share the same head SHA, need to split branches"),
            "pr_branch_topology_task",
        )

    def test_rebase_and_cherry_pick(self):
        self.assertEqual(
            tc.classify_task("rebase and cherry-pick commits from feature branch"),
            "pr_branch_topology_task",
        )

    def test_force_with_lease(self):
        self.assertEqual(
            tc.classify_task("use force-with-lease to update the remote branch safely"),
            "pr_branch_topology_task",
        )

    def test_branch_split(self):
        self.assertEqual(
            tc.classify_task("split branch because two features were developed together"),
            "pr_branch_topology_task",
        )

    def test_commit_graph_analysis(self):
        self.assertEqual(
            tc.classify_task("analyze commit graph to understand branch divergence"),
            "pr_branch_topology_task",
        )

    def test_pr_verification(self):
        self.assertEqual(
            tc.classify_task("PR verification: check head SHA matches expected value"),
            "pr_branch_topology_task",
        )

    def test_merge_conflict_resolution(self):
        self.assertEqual(
            tc.classify_task("resolve merge conflict between main and feature branch"),
            "pr_branch_topology_task",
        )

    def test_branch_topology(self):
        self.assertEqual(
            tc.classify_task("analyze branch topology before the release"),
            "pr_branch_topology_task",
        )


# ============================================================================
# Unsupported task classification
# ============================================================================


class TestUnsupportedTaskClassification(unittest.TestCase):
    """Verify that unrecognisable input is classified as unsupported_task."""

    def test_empty_string(self):
        self.assertEqual(tc.classify_task(""), "unsupported_task")

    def test_whitespace_only(self):
        self.assertEqual(tc.classify_task("   \t\n  "), "unsupported_task")

    def test_garbage_input(self):
        self.assertEqual(tc.classify_task("asdf qwer zxcv 12345 !@#$%"), "unsupported_task")

    def test_generic_question(self):
        self.assertEqual(
            tc.classify_task("what is the weather today?"),
            "unsupported_task",
        )

    def test_off_topic_input(self):
        self.assertEqual(
            tc.classify_task("I want to order a pizza with extra cheese"),
            "unsupported_task",
        )


# ============================================================================
# Case insensitivity
# ============================================================================


class TestCaseInsensitivity(unittest.TestCase):
    """Verify that classification ignores letter case."""

    def test_uppercase_patch_task(self):
        self.assertEqual(
            tc.classify_task("IMPLEMENT NEW FEATURE FOR DASHBOARD"),
            "patch_task",
        )

    def test_mixed_case_patch_review(self):
        self.assertEqual(
            tc.classify_task("ReViEw ThIs PaTcH for security"),
            "patch_review_task",
        )

    def test_uppercase_pr_branch_topology(self):
        self.assertEqual(
            tc.classify_task("FORCE PUSH AFTER REBASE ON MAIN"),
            "pr_branch_topology_task",
        )

    def test_mixed_case_review_trumps_fix(self):
        # "REVIEW" weighted higher than "FIX" -> patch_review_task
        self.assertEqual(
            tc.classify_task("CODE REVIEW the bug FIX"),
            "patch_review_task",
        )


# ============================================================================
# Chinese / bilingual input
# ============================================================================


class TestChineseBilingualInput(unittest.TestCase):
    """Verify classification works with Chinese and Chinese-English mixed input."""

    def test_chinese_patch_task(self):
        self.assertEqual(
            tc.classify_task("实现新的登录功能"),
            "patch_task",
        )

    def test_chinese_patch_review(self):
        self.assertEqual(
            tc.classify_task("审查代码补丁的安全性"),
            "patch_review_task",
        )

    def test_chinese_pr_branch_topology(self):
        self.assertEqual(
            tc.classify_task("PR分支拓扑需要强制推送"),
            "pr_branch_topology_task",
        )

    def test_chinese_mixed_patch_task(self):
        self.assertEqual(
            tc.classify_task("请修复bug并优化performance"),
            "patch_task",
        )

    def test_chinese_mixed_review(self):
        self.assertEqual(
            tc.classify_task("请review这个patch的代码修改"),
            "patch_review_task",
        )

    def test_chinese_unsupported(self):
        self.assertEqual(
            tc.classify_task("今天天气很好"),
            "unsupported_task",
        )


# ============================================================================
# Mixed / overlapping signals
# ============================================================================


class TestMixedSignals(unittest.TestCase):
    """Verify correct classification when multiple categories are signalled."""

    def test_review_trumps_fix(self):
        # "review" (weight 3) beats "fix" (weight 2) and "bug" (weight 2)
        # But "code review" has weight 5 which easily beats everything
        self.assertEqual(
            tc.classify_task("fix the bug and review the code"),
            "patch_review_task",
        )

    def test_branch_topology_trumps_review(self):
        # "force push" (weight 5) and "rebase" (weight 5) together
        # easily outweigh "review" (weight 3)
        self.assertEqual(
            tc.classify_task("review the force push and rebase plan"),
            "pr_branch_topology_task",
        )

    def test_branch_surgery_trumps_patch(self):
        # "branch topology" (weight 5) + "rebase" (weight 5)
        # vs "fix" (weight 2) + "bug" (weight 2)
        self.assertEqual(
            tc.classify_task("fix bug in branch topology after rebase"),
            "pr_branch_topology_task",
        )

    def test_multiple_patch_signals(self):
        # Only patch_task keywords present
        self.assertEqual(
            tc.classify_task("implement fix refactor optimize build enhance"),
            "patch_task",
        )


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases(unittest.TestCase):
    """Edge-case and robustness tests."""

    def test_very_long_input(self):
        # Generate a long task description with a keyword at the end.
        prefix = "this is background context. " * 500
        task = prefix + "implement a new feature for the API"
        self.assertEqual(tc.classify_task(task), "patch_task")

    def test_newlines_and_special_chars(self):
        task = "Review\nthis\tpatch\rfor\t\tsecurity issues!"
        self.assertEqual(tc.classify_task(task), "patch_review_task")

    def test_keyword_as_substring(self):
        # "refactoring" should match the keyword "refactor"
        self.assertEqual(
            tc.classify_task("refactoring the entire module structure"),
            "patch_task",
        )

    def test_single_character_input(self):
        self.assertEqual(tc.classify_task("x"), "unsupported_task")

    def test_numeric_only_input(self):
        self.assertEqual(tc.classify_task("12345 67890"), "unsupported_task")


# ============================================================================
# Helper function tests
# ============================================================================


class TestHelperFunctions(unittest.TestCase):
    """Verify the convenience predicate functions."""

    def test_is_patch_task(self):
        self.assertTrue(tc.is_patch_task("fix the bug"))
        self.assertFalse(tc.is_patch_task("review the code"))

    def test_is_patch_review_task(self):
        self.assertTrue(tc.is_patch_review_task("review the patch"))
        self.assertFalse(tc.is_patch_review_task("fix the bug"))

    def test_is_pr_branch_topology_task(self):
        self.assertTrue(tc.is_pr_branch_topology_task("force push the branch"))
        self.assertFalse(tc.is_pr_branch_topology_task("implement login"))

    def test_is_supported_task(self):
        self.assertTrue(tc.is_supported_task("fix the bug"))
        self.assertFalse(tc.is_supported_task(""))
        self.assertFalse(tc.is_supported_task("hello world"))


# ============================================================================
# task_type_description tests
# ============================================================================


class TestTaskTypeDescription(unittest.TestCase):
    """Verify human-readable descriptions for each task type."""

    def test_valid_descriptions(self):
        for task_type in [
            "patch_task",
            "patch_review_task",
            "pr_branch_topology_task",
            "unsupported_task",
        ]:
            desc = tc.task_type_description(task_type)
            self.assertIsInstance(desc, str)
            self.assertGreater(len(desc), 10, f"Description too short for {task_type}")

    def test_invalid_type_raises_value_error(self):
        with self.assertRaises(ValueError):
            tc.task_type_description("imaginary_task_type")

    def test_invalid_type_error_message(self):
        with self.assertRaises(ValueError) as ctx:
            tc.task_type_description("nonexistent")
        self.assertIn("nonexistent", str(ctx.exception))
        self.assertIn("Unknown task type", str(ctx.exception))


# ============================================================================
# Priority / tie-breaking tests
# ============================================================================


class TestPriorityTieBreaking(unittest.TestCase):
    """Verify the priority order when keyword scores tie."""

    def test_fallback_to_unsupported_when_no_keywords(self):
        self.assertEqual(tc.classify_task("completely unrelated text here"), "unsupported_task")

    def test_classify_task_never_returns_none(self):
        """classify_task must always return a valid string."""
        inputs = [
            "",
            "   ",
            "fix",
            "review",
            "force push",
            "hello",
            "..." * 100,
        ]
        for inp in inputs:
            result = tc.classify_task(inp)
            self.assertIn(
                result,
                [
                    "patch_task",
                    "patch_review_task",
                    "pr_branch_topology_task",
                    "unsupported_task",
                ],
                f"Unexpected result {result!r} for input {inp!r}",
            )


# ============================================================================
# Keyword weight ordering tests
# ============================================================================


class TestKeywordWeighting(unittest.TestCase):
    """Verify that higher-weighted keywords produce expected classifications."""

    def test_code_review_beats_simple_review(self):
        # "code review" has weight 5, "change" has weight 1
        self.assertEqual(
            tc.classify_task("code review the change"),
            "patch_review_task",
        )

    def test_branch_split_beats_patch_signals(self):
        # "branch split" (5) + "head sha" (5) easily beats
        # "fix" (2) + "implement" (2) + "update" (1)
        self.assertEqual(
            tc.classify_task("fix implement update the branch split and head sha"),
            "pr_branch_topology_task",
        )

    def test_accumulated_patch_signals(self):
        # Multiple patch_task keywords accumulate: 2+2+3+2+3=12
        self.assertEqual(
            tc.classify_task("fix bug implement refactor add feature"),
            "patch_task",
        )


class TestCLI(unittest.TestCase):
    """Tests for the new argparse-based CLI."""

    def test_help_flag_works(self):
        """--help should print usage and exit 0."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent /
             "scripts" / "task_classifier.py"), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout.lower())

    def test_list_types_works(self):
        """--list-types should list all 4 types."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent /
             "scripts" / "task_classifier.py"), "--list-types"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("patch_task", result.stdout)
        self.assertIn("patch_review_task", result.stdout)
        self.assertIn("pr_branch_topology_task", result.stdout)
        self.assertIn("unsupported_task", result.stdout)

    def test_positional_classification(self):
        """Positional args should still classify."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent /
             "scripts" / "task_classifier.py"), "implement", "login", "feature"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("patch_task", result.stdout)

    def test_file_flag(self):
        """--file should read from a file."""
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("fix the authentication bug")
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent.parent /
                 "scripts" / "task_classifier.py"), "--file", tmp],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("patch_task", result.stdout)
        finally:
            os.unlink(tmp)

    def test_empty_input_exits(self):
        """Empty input should exit with error."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent /
             "scripts" / "task_classifier.py")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
