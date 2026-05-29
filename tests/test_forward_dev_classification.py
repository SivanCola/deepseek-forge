"""Unit tests for the forward_development_task classification.

Extends existing classification tests with the new forward_development_task category.
"""

from __future__ import annotations

import os
import sys
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

import task_classifier as tc


class TestForwardDevelopmentTaskClassification(unittest.TestCase):
    """Verify that forward development task descriptions are classified correctly."""

    def test_forward_development(self):
        self.assertEqual(
            tc.classify_task("forward development of a new user dashboard"),
            "forward_development_task",
        )

    def test_development_loop(self):
        self.assertEqual(
            tc.classify_task("run the development loop for the payment module"),
            "forward_development_task",
        )

    def test_acceptance_criteria(self):
        self.assertEqual(
            tc.classify_task("implement from scratch with acceptance criteria"),
            "forward_development_task",
        )

    def test_build_from_scratch(self):
        self.assertEqual(
            tc.classify_task("build from scratch a REST API for orders"),
            "forward_development_task",
        )

    def test_full_development_cycle(self):
        self.assertEqual(
            tc.classify_task("full development cycle for the auth module"),
            "forward_development_task",
        )

    def test_expand_plan(self):
        self.assertEqual(
            tc.classify_task("expand plan for the notification system"),
            "forward_development_task",
        )

    def test_dev_loop(self):
        self.assertEqual(
            tc.classify_task("run dev loop"),
            "forward_development_task",
        )

    def test_implement_todo(self):
        self.assertEqual(
            tc.classify_task("implement_todo for item 3"),
            "forward_development_task",
        )

    def test_acceptance_md_reference(self):
        self.assertEqual(
            tc.classify_task("check acceptance.md and implement todos"),
            "forward_development_task",
        )

    def test_codex_regulated(self):
        self.assertEqual(
            tc.classify_task("codex-regulated forward development"),
            "forward_development_task",
        )

    def test_chinese_forward_development(self):
        self.assertEqual(
            tc.classify_task("正向开发一个新的用户管理模块"),
            "forward_development_task",
        )

    def test_chinese_acceptance_criteria(self):
        self.assertEqual(
            tc.classify_task("按照验收标准进行开发循环"),
            "forward_development_task",
        )

    def test_chinese_full_dev(self):
        self.assertEqual(
            tc.classify_task("完整开发电商平台的购物车功能"),
            "forward_development_task",
        )

    def test_chinese_build_from_scratch(self):
        self.assertEqual(
            tc.classify_task("从头构建搜索服务"),
            "forward_development_task",
        )


class TestForwardDevPriority(unittest.TestCase):
    """Verify forward_development_task takes priority over other categories."""

    def test_forward_dev_trumps_patch(self):
        # "build from scratch" (weight 9) beats "fix" (weight 2)
        self.assertEqual(
            tc.classify_task("build from scratch and fix bugs"),
            "forward_development_task",
        )

    def test_forward_dev_trumps_branch_topology(self):
        # "acceptance criteria" (weight 10) beats "force push" (weight 5)
        self.assertEqual(
            tc.classify_task("acceptance criteria for the force push workflow"),
            "forward_development_task",
        )

    def test_forward_dev_trumps_review(self):
        # "forward development" (weight 10) beats "review" (weight 3)
        self.assertEqual(
            tc.classify_task("forward development with code review"),
            "forward_development_task",
        )

    def test_forward_dev_trumps_all(self):
        self.assertEqual(
            tc.classify_task("forward development loop with acceptance criteria: implement fix review force push"),
            "forward_development_task",
        )


class TestForwardDevConvenienceFunctions(unittest.TestCase):
    """Verify the is_forward_development_task helper."""

    def test_is_forward_development_task(self):
        self.assertTrue(tc.is_forward_development_task("forward development of login"))
        self.assertTrue(tc.is_forward_development_task("build from scratch"))
        self.assertFalse(tc.is_forward_development_task("fix the bug"))
        self.assertFalse(tc.is_forward_development_task("review the code"))
        self.assertFalse(tc.is_forward_development_task(""))


class TestForwardDevCLI(unittest.TestCase):
    """Verify --list-types includes forward_development_task."""

    def test_list_types_includes_forward_dev(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH / "task_classifier.py"), "--list-types"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("forward_development_task", result.stdout)


if __name__ == "__main__":
    unittest.main()
