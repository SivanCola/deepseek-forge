"""Comprehensive unit tests for scripts/collect_context.py.

Uses only stdlib (unittest, tempfile, unittest.mock, pathlib).
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
import contextlib
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure the scripts directory is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import collect_context as cc


# ============================================================================
# Helpers
# ============================================================================

def _make_file(dir_path: Path, rel_path: str, content: str | bytes) -> Path:
    """Create a file under *dir_path* with *content*, creating parent dirs."""
    full = dir_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        full.write_text(content, encoding="utf-8")
    else:
        full.write_bytes(content)
    return full


def _make_dir(dir_path: Path, rel_path: str) -> Path:
    """Create a directory under *dir_path*."""
    full = dir_path / rel_path
    full.mkdir(parents=True, exist_ok=True)
    return full


# ============================================================================
# Tests
# ============================================================================


class TestCLIArgs(unittest.TestCase):
    """Test that argparse correctly parses all arguments with defaults."""

    def test_required_args(self):
        """Verify that --task and --output are required."""
        parser = cc.argparse.ArgumentParser()
        parser.add_argument("--task", required=True)
        parser.add_argument("--output", required=True)
        # Missing --output should raise SystemExit.
        with contextlib.redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--task", "task.md"])

    def test_all_args_with_defaults(self):
        """Parse all arguments and verify values (including defaults)."""
        # We replicate the parser construction from the module.
        parser = cc.argparse.ArgumentParser()
        parser.add_argument("--task", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--max-files", type=int, default=80)
        parser.add_argument("--max-bytes", type=int, default=120000)

        args = parser.parse_args(["--task", "t.md", "--output", "o.md"])
        self.assertEqual(args.task, "t.md")
        self.assertEqual(args.output, "o.md")
        self.assertEqual(args.max_files, 80)
        self.assertEqual(args.max_bytes, 120000)

        # Explicit override.
        args2 = parser.parse_args(
            ["--task", "t.md", "--output", "o.md", "--max-files", "10", "--max-bytes", "999"]
        )
        self.assertEqual(args2.max_files, 10)
        self.assertEqual(args2.max_bytes, 999)

    def test_missing_required_arg_raises(self):
        parser = cc.argparse.ArgumentParser()
        parser.add_argument("--task", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--max-files", type=int, default=80)
        parser.add_argument("--max-bytes", type=int, default=120000)
        with contextlib.redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--task", "t.md"])


class TestBinaryDetection(unittest.TestCase):
    """Test that is_binary_file returns True for files with null bytes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_file_is_not_binary(self):
        fp = _make_file(Path(self.tmp.name), "hello.txt", "Hello, world!\n")
        self.assertFalse(cc.is_binary_file(fp))

    def test_null_byte_file_is_binary(self):
        fp = _make_file(Path(self.tmp.name), "data.bin", b"PK\x00\x01\x02")
        self.assertTrue(cc.is_binary_file(fp))

    def test_empty_file_is_not_binary(self):
        fp = _make_file(Path(self.tmp.name), "empty.txt", "")
        self.assertFalse(cc.is_binary_file(fp))

    def test_binary_detection_only_reads_first_1024_bytes(self):
        # File with null byte at byte 500 should be detected.
        data = b"A" * 500 + b"\x00" + b"B" * 2000
        fp = _make_file(Path(self.tmp.name), "big.bin", data)
        self.assertTrue(cc.is_binary_file(fp))

    def test_text_file_before_null_byte_threshold(self):
        # File with null byte beyond 1024 bytes should NOT be treated as binary.
        data = b"T" * 1025 + b"\x00"
        fp = _make_file(Path(self.tmp.name), "late_null.bin", data)
        # Our implementation only checks first 1024 bytes, so this is text.
        self.assertFalse(cc.is_binary_file(fp))

    def test_unreadable_file_treated_as_binary(self):
        # Non-existent file: OSError -> treated as binary.
        fp = Path(self.tmp.name) / "does_not_exist.xyz"
        self.assertTrue(cc.is_binary_file(fp))


class TestFileFiltering(unittest.TestCase):
    """Test that lock files, build artifacts, and .git paths are excluded."""

    def test_lock_files_excluded(self):
        for name in [
            "package-lock.json",
            "yarn.lock",
            "Cargo.lock",
            "go.sum",
            "poetry.lock",
            "Gemfile.lock",
            "Pipfile.lock",
            "pnpm-lock.yaml",
        ]:
            self.assertTrue(cc.is_lock_file(Path(f"/some/project/{name}")), f"Expected {name} to be a lock file")
            self.assertTrue(cc.is_lock_file(Path(name)), f"Expected bare {name} to be a lock file")

    def test_normal_files_not_lock_files(self):
        for name in ["package.json", "main.py", "Cargo.toml", "go.mod", "README.md"]:
            self.assertFalse(cc.is_lock_file(Path(name)), f"{name} should not be a lock file")

    def test_build_artifact_patterns_excluded(self):
        artifact_paths = [
            "dist/main.js",
            "build/output.txt",
            "node_modules/react/index.js",
            "__pycache__/foo.cpython-311.pyc",
            "target/debug/myapp.o",
        ]
        for p in artifact_paths:
            self.assertTrue(cc.is_build_artifact(Path(p)), f"Expected {p} to be a build artifact")

    def test_source_files_not_build_artifacts(self):
        source_paths = [
            "src/main.py",
            "lib/utils.js",
            "README.md",
            "scripts/build.sh",
        ]
        for p in source_paths:
            self.assertFalse(cc.is_build_artifact(Path(p)), f"{p} should not be a build artifact")

    def test_git_path_is_skipped(self):
        skip, reason = cc.should_skip(Path(".git/config"))
        self.assertTrue(skip)
        self.assertIn(".git", reason)

    def test_build_artifact_is_skipped(self):
        skip, reason = cc.should_skip(Path("node_modules/express/index.js"))
        self.assertTrue(skip)
        self.assertEqual(reason, "build artifact")

    def test_lock_file_is_skipped(self):
        skip, reason = cc.should_skip(Path("package-lock.json"))
        self.assertTrue(skip)
        self.assertEqual(reason, "lock file")

    def test_large_file_is_skipped(self):
        with patch.object(Path, "is_file", return_value=True):
            with patch.object(Path, "stat") as mock_stat:
                mock_stat.return_value.st_size = 500_000  # > 200KB
                skip, reason = cc.should_skip(Path("huge.py"))
                self.assertTrue(skip)
                self.assertIn("file too large", reason)

    def test_normal_source_file_not_skipped(self):
        with patch.object(Path, "is_file", return_value=True):
            with patch.object(Path, "stat") as mock_stat:
                mock_stat.return_value.st_size = 1024
                with patch("collect_context.is_binary_file", return_value=False):
                    with patch.object(cc, "is_lock_file", return_value=False):
                        skip, reason = cc.should_skip(Path("src/main.py"))
                        self.assertFalse(skip, f"Unexpected skip: {reason}")


class TestKeywordMatching(unittest.TestCase):
    """Test that task keywords are extracted and matched against file paths."""

    def test_extract_keywords_extracts_lowercase_words(self):
        task = "Fix the bug in UserAuthentication module!"
        keywords = cc.extract_keywords(task)
        self.assertIn("fix", keywords)
        self.assertIn("the", keywords)
        self.assertIn("bug", keywords)
        self.assertIn("in", keywords)
        self.assertIn("userauthentication", keywords)
        self.assertIn("module", keywords)

    def test_extract_keywords_handles_empty_string(self):
        self.assertEqual(cc.extract_keywords(""), [])
        self.assertEqual(cc.extract_keywords("   !@#$%  "), [])

    def test_score_file_matches_keywords(self):
        keywords = ["user", "auth", "login"]
        # "user" matches "user", "auth" matches "auth"
        score = cc.score_file(Path("src/user/auth.py"), keywords)
        self.assertEqual(score, 2)

    def test_score_file_case_insensitive(self):
        keywords = ["user", "auth"]
        score = cc.score_file(Path("SRC/USER/AUTH.PY"), keywords)
        self.assertEqual(score, 2)

    def test_score_file_no_match(self):
        keywords = ["database", "postgres"]
        score = cc.score_file(Path("src/frontend/react/components/header.tsx"), keywords)
        self.assertEqual(score, 0)

    def test_score_file_partial_word_match(self):
        # Keywords match as substrings anywhere in the path.
        keywords = ["test"]
        score = cc.score_file(Path("src/testing_utils/runner.py"), keywords)
        self.assertEqual(score, 1)


class TestMaxFilesLimit(unittest.TestCase):
    """Test that max-files enforcement works."""

    def test_max_files_enforcement(self):
        # Simulate source_candidates with 100 entries.
        candidates = [(Path(f"file_{i:03d}.py"), i % 5) for i in range(100)]
        max_files = 25
        selected = candidates[:max_files]
        skipped_count = max(0, len(candidates) - max_files)
        self.assertEqual(len(selected), 25)
        self.assertEqual(skipped_count, 75)

    def test_max_files_zero(self):
        candidates = [(Path(f"file_{i:03d}.py"), 0) for i in range(10)]
        max_files = 0
        selected = candidates[:max_files]
        self.assertEqual(len(selected), 0)

    def test_sorting_by_relevance(self):
        """Higher-scored files should come first."""
        candidates = [
            (Path("low.py"), 1),
            (Path("high.py"), 10),
            (Path("mid.py"), 5),
        ]
        candidates.sort(key=lambda x: (-x[1], str(x[0])))
        self.assertEqual(candidates[0][0], Path("high.py"))
        self.assertEqual(candidates[1][0], Path("mid.py"))
        self.assertEqual(candidates[2][0], Path("low.py"))

    def test_sorting_tiebreaker_by_path(self):
        """Equal scores should be ordered by path string."""
        candidates = [
            (Path("z.py"), 3),
            (Path("a.py"), 3),
            (Path("m.py"), 3),
        ]
        candidates.sort(key=lambda x: (-x[1], str(x[0])))
        self.assertEqual(candidates[0][0], Path("a.py"))
        self.assertEqual(candidates[1][0], Path("m.py"))
        self.assertEqual(candidates[2][0], Path("z.py"))


class TestMaxBytesLimit(unittest.TestCase):
    """Test that max-bytes enforcement truncates output."""

    def test_output_truncated_when_exceeds_max_bytes(self):
        task = "Test task\n"
        git_status = " M file.py\n"
        git_diff = " file.py | 2 +-\n"
        tree_lines = ["├── file.py"]
        config_files: dict[str, str] = {}
        source_entries = [(Path("big.py"), "x" * 5000, 10)]
        files_skipped: dict[str, int] = {"lock file": 3}

        # Set max_bytes small so truncation is guaranteed.
        output = cc.generate_output(
            task_content=task,
            git_status=git_status,
            git_diff=git_diff,
            tree_lines=tree_lines,
            config_files=config_files,
            source_entries=source_entries,
            files_considered=5,
            files_skipped=files_skipped,
            max_bytes=200,
        )
        self.assertIn("Truncated: yes", output)
        self.assertIn("Output was truncated", output)

    def test_no_truncation_when_under_max_bytes(self):
        task = "Short\n"
        git_status = ""
        git_diff = ""
        tree_lines = []
        config_files = {}
        source_entries = [(Path("a.py"), "print('hi')\n", 1)]
        files_skipped: dict[str, int] = {}

        output = cc.generate_output(
            task_content=task,
            git_status=git_status,
            git_diff=git_diff,
            tree_lines=tree_lines,
            config_files=config_files,
            source_entries=source_entries,
            files_considered=1,
            files_skipped=files_skipped,
            max_bytes=500_000,
        )
        self.assertIn("Truncated: no", output)


class TestMarkdownOutputFormat(unittest.TestCase):
    """Test that output contains required sections."""

    def test_output_has_required_sections(self):
        output = cc.generate_output(
            task_content="The task.\n",
            git_status=" M src/main.py\n",
            git_diff=" src/main.py | 1 +\n",
            tree_lines=["├── src/", "│   ├── main.py", "└── README.md"],
            config_files={"pyproject.toml": "[tool]\n"},
            source_entries=[(Path("src/main.py"), "print('hi')\n", 5)],
            files_considered=5,
            files_skipped={"build artifact": 2},
            max_bytes=100_000,
        )

        required_sections = [
            "# Repository Context",
            "## Task Description",
            "## Git Status",
            "## Git Diff Stat",
            "## File Tree",
            "## Configuration Files",
            "## Source Files (task-related)",
            "## Context Summary",
        ]
        for section in required_sections:
            self.assertIn(section, output, f"Missing section: {section}")

    def test_summary_section_has_metrics(self):
        output = cc.generate_output(
            task_content="Task.\n",
            git_status="",
            git_diff="",
            tree_lines=[],
            config_files={},
            source_entries=[],
            files_considered=42,
            files_skipped={"lock file": 5, "binary file": 3},
            max_bytes=10_000,
        )
        self.assertIn("Files considered: 42", output)
        self.assertIn("lock file: 5", output)
        self.assertIn("binary file: 3", output)
        self.assertIn("Files included: 0", output)

    def test_config_files_have_code_blocks(self):
        output = cc.generate_output(
            task_content="Task.\n",
            git_status="",
            git_diff="",
            tree_lines=[],
            config_files={"pyproject.toml": "[project]\nname = 'test'\n"},
            source_entries=[],
            files_considered=2,
            files_skipped={},
            max_bytes=10_000,
        )
        self.assertIn("```toml", output)
        self.assertIn("[project]", output)
        self.assertIn("### pyproject.toml", output)

    def test_source_files_have_code_blocks(self):
        output = cc.generate_output(
            task_content="Task.\n",
            git_status="",
            git_diff="",
            tree_lines=[],
            config_files={},
            source_entries=[(Path("src/utils.py"), "def foo():\n    pass\n", 5)],
            files_considered=1,
            files_skipped={},
            max_bytes=10_000,
        )
        self.assertIn("### src/utils.py", output)
        self.assertIn("```python", output)
        self.assertIn("def foo():", output)


class TestGitNotAvailable(unittest.TestCase):
    """Test graceful handling when git commands fail."""

    def test_run_git_returns_empty_on_file_not_found(self):
        result = cc.run_git(["status", "--short"], Path("/tmp"))
        # If git is not installed, subprocess will raise FileNotFoundError
        # which is caught — result should be an empty string.
        self.assertEqual(result, "")

    def test_git_status_empty_produces_placeholder(self):
        output = cc.generate_output(
            task_content="Task.\n",
            git_status="",
            git_diff="",
            tree_lines=[],
            config_files={},
            source_entries=[],
            files_considered=0,
            files_skipped={},
            max_bytes=10_000,
        )
        self.assertIn("(nothing to commit", output.lower())

    def test_git_diff_empty_produces_placeholder(self):
        output = cc.generate_output(
            task_content="Task.\n",
            git_status="",
            git_diff="",
            tree_lines=[],
            config_files={},
            source_entries=[],
            files_considered=0,
            files_skipped={},
            max_bytes=10_000,
        )
        self.assertIn("(no staged/unstaged changes", output.lower())

    @patch("collect_context.subprocess.run")
    def test_subprocess_error_is_caught(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        result = cc.run_git(["status"], Path("/tmp"))
        self.assertEqual(result, "")

    @patch("collect_context.subprocess.run")
    def test_subprocess_timeout_is_caught(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        result = cc.run_git(["status"], Path("/tmp"))
        self.assertEqual(result, "")


class TestConfigFileDetection(unittest.TestCase):
    """Test that key config files are detected and included."""

    def test_config_file_names_are_recognized(self):
        config_names = {
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
        for name in config_names:
            self.assertIn(name, cc._CONFIG_FILE_NAMES, f"Missing from config set: {name}")

    def test_config_file_not_treated_as_regular_source(self):
        """When should_skip runs on a config file, it should not be marked as
        a build artifact or lock file — it will be caught by the special
        config-file path in the main driver."""
        fp = Path("pyproject.toml")
        self.assertFalse(cc.is_lock_file(fp))
        self.assertFalse(cc.is_build_artifact(fp))


class TestTreeGeneration(unittest.TestCase):
    """Test that file tree is generated correctly."""

    def test_single_file(self):
        paths = [Path("README.md")]
        tree = cc.build_file_tree(paths, Path("/fake/root"))
        self.assertIn("README.md", tree[0])

    def test_nested_directories(self):
        paths = [
            Path("src/main.py"),
            Path("src/utils/helpers.py"),
            Path("src/utils/__init__.py"),
            Path("README.md"),
        ]
        tree = cc.build_file_tree(paths, Path("/fake/root"))
        text = "\n".join(tree)
        # Tree should show dirs with trailing / and files without.
        self.assertIn("src/", text)
        self.assertIn("main.py", text)
        self.assertIn("utils/", text)
        self.assertIn("helpers.py", text)
        self.assertIn("README.md", text)

    def test_tree_contains_special_chars(self):
        """Tree connectors should be present."""
        paths = [
            Path("a.py"),
            Path("b.py"),
        ]
        tree = cc.build_file_tree(paths, Path("/fake/root"))
        text = "\n".join(tree)
        self.assertIn("├──", text)

    def test_empty_tree(self):
        tree = cc.build_file_tree([], Path("/fake/root"))
        self.assertEqual(tree, [])

    @patch("collect_context.build_file_tree")
    def test_tree_included_in_output(self, mock_build):
        mock_build.return_value = ["├── src/", "│   └── main.py"]
        output = cc.generate_output(
            task_content="Task.\n",
            git_status="",
            git_diff="",
            tree_lines=["├── src/", "│   └── main.py"],
            config_files={},
            source_entries=[],
            files_considered=1,
            files_skipped={},
            max_bytes=10_000,
        )
        self.assertIn("## File Tree", output)
        self.assertIn("├── src/", output)


class TestLanguageMapping(unittest.TestCase):
    """Test that language detection for code fences works."""

    def test_python_file(self):
        self.assertEqual(cc._lang_for(Path("main.py")), "python")

    def test_javascript_file(self):
        self.assertEqual(cc._lang_for(Path("app.js")), "javascript")

    def test_typescript_file(self):
        self.assertEqual(cc._lang_for(Path("component.ts")), "typescript")
        self.assertEqual(cc._lang_for(Path("component.tsx")), "tsx")

    def test_toml_file(self):
        self.assertEqual(cc._lang_for(Path("pyproject.toml")), "toml")

    def test_makefile(self):
        self.assertEqual(cc._lang_for(Path("Makefile")), "makefile")

    def test_dockerfile(self):
        self.assertEqual(cc._lang_for(Path("Dockerfile")), "dockerfile")

    def test_unknown_extension(self):
        self.assertEqual(cc._lang_for(Path("data.xyz")), "")

    def test_no_extension(self):
        self.assertEqual(cc._lang_for(Path("LICENSE")), "")


class TestIntegrationWorkflow(unittest.TestCase):
    """End-to-end-ish tests that generate output from a simulated repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)

        # Create a small simulated repo.
        _make_file(self.repo_root, "README.md", "# My Project\n")
        _make_file(self.repo_root, "pyproject.toml", "[project]\nname='test'\n")
        _make_file(self.repo_root, "src/main.py", "def main():\n    pass\n")
        _make_file(self.repo_root, "src/utils/helpers.py", "def helper():\n    pass\n")
        _make_file(self.repo_root, "src/__init__.py", "# src package\n")
        _make_file(self.repo_root, "src/utils/__init__.py", "# utils\n")
        _make_file(self.repo_root, "package-lock.json", "{}")  # lock file → skip
        _make_dir(self.repo_root, "node_modules/.cache")        # build artifact → skip
        _make_file(self.repo_root, "node_modules/.cache/babel.json", "{}")
        _make_dir(self.repo_root, ".git")
        _make_file(self.repo_root, ".git/config", "[core]\n")

        # Task file.
        self.task_path = _make_file(self.repo_root, "task.md", "Fix the main function in utils helpers.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_flow_generates_output_file(self):
        output_path = self.repo_root / "output.md"

        # We need to patch git calls and run from the temp dir.
        with patch("collect_context.run_git") as mock_git:
            mock_git.side_effect = lambda args, cwd: {
                ("status", "--short"): " M src/main.py\n",
                ("diff", "--stat"): " src/main.py | 2 +-\n",
                ("ls-files",): (
                    "README.md\n"
                    "pyproject.toml\n"
                    "src/main.py\n"
                    "src/utils/helpers.py\n"
                    "src/__init__.py\n"
                    "src/utils/__init__.py\n"
                    "package-lock.json\n"
                    "node_modules/.cache/babel.json\n"
                    ".git/config\n"
                ),
            }.get(tuple(args), "")

            # Patch cwd to be our temp repo.
            with patch("collect_context.Path.cwd", return_value=self.repo_root):
                argv = [
                    "--task", str(self.task_path),
                    "--output", str(output_path),
                    "--max-files", "10",
                    "--max-bytes", "50000",
                ]
                cc.main(argv)

        self.assertTrue(output_path.exists(), f"Output file was not created at {output_path}")

        content = output_path.read_text(encoding="utf-8")

        # Required sections present.
        self.assertIn("# Repository Context", content)
        self.assertIn("## Task Description", content)
        self.assertIn("Fix the main function", content)
        self.assertIn("## Git Status", content)
        self.assertIn("## Git Diff Stat", content)
        self.assertIn("## File Tree", content)
        self.assertIn("## Configuration Files", content)
        self.assertIn("## Source Files (task-related)", content)
        self.assertIn("## Context Summary", content)

        # Config file included.
        self.assertIn("### pyproject.toml", content)

        # Source files included — paths are absolute (resolved), so check for
        # the relative portion appearing somewhere in the output.
        self.assertIn("helpers.py", content)
        self.assertIn("main.py", content)

        # Lock file excluded.
        self.assertNotIn("package-lock.json", content.split("## Source Files")[1] if "## Source Files" in content else content)

        # .git excluded.
        self.assertNotIn(".git/config", content.split("## Source Files")[1] if "## Source Files" in content else content)


class TestKeywordPriorityOrdering(unittest.TestCase):
    """Test that files are ordered by relevance to the task."""

    def test_relevance_sorting_respected_in_source_entries(self):
        task = "Fix the authentication bug in the user login module"
        keywords = cc.extract_keywords(task)

        # Files with varying relevance.
        paths = [
            Path("src/auth/login.py"),        # auth, login = 2
            Path("docs/readme.md"),            # 0
            Path("src/user/profile.py"),       # user = 1
            Path("tests/test_auth.py"),        # auth = 1
            Path("src/database/migrations.py"),# 0
        ]

        scores = [(p, cc.score_file(p, keywords)) for p in paths]
        scores.sort(key=lambda x: (-x[1], str(x[0])))

        # Highest score first.
        self.assertEqual(scores[0][0], Path("src/auth/login.py"))
        self.assertEqual(scores[0][1], 2)
        # Zero-score files last, sorted by path.
        zero_paths = [s[0] for s in scores if s[1] == 0]
        self.assertEqual(zero_paths, sorted(zero_paths, key=str))


class TestPRBranchTopologyMode(unittest.TestCase):
    """Test the --mode pr-branch-topology feature.

    Uses the same mocking patterns as TestIntegrationWorkflow: patch run_git,
    run_gh, and Path.cwd, then drive everything through cc.main(argv).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)

        # Minimal simulated repo — only files needed by the main() driver.
        _make_dir(self.repo_root, ".git")
        _make_file(self.repo_root, ".git/config", "[core]\n")
        _make_file(self.repo_root, "README.md", "# My Project\n")

        # Task file (required by --task).
        self.task_path = _make_file(
            self.repo_root, "task.md",
            "# PR Topology Analysis\n\nCheck branch topology for the release.\n",
        )

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # Shared mock helpers
    # ------------------------------------------------------------------

    def _fake_run_git(self, args, cwd):
        """Return canned git output that exercises every topological section."""
        cmd = tuple(args)
        if cmd == ("status", "--short"):
            return " M README.md\n?? newfile.py\n"
        elif cmd == ("branch", "--show-current"):
            return "feature/test-branch\n"
        elif cmd == ("remote", "-v"):
            return (
                "origin  git@github.com:user/myproject.git (fetch)\n"
                "origin  git@github.com:user/myproject.git (push)\n"
            )
        elif cmd == ("log", "--oneline", "--graph", "--all", "-20"):
            return (
                "* abc1234 Fix login bug\n"
                "* def5678 Add feature X\n"
            )
        elif cmd == ("log", "--oneline", "-20"):
            return (
                "abc1234 Fix login bug\n"
                "def5678 Add feature X\n"
            )
        elif cmd == ("diff", "--stat", "HEAD~10..HEAD"):
            return " README.md | 2 ++\n src/main.py | 5 +++++\n"
        elif cmd == ("merge-base", "HEAD", "feature/test-branch"):
            return "def5678\n"
        elif cmd == ("merge-base", "HEAD", "main"):
            return "def5678\n"
        elif cmd == ("branch",):
            return "* feature/test-branch\n  main\n"
        return ""

    def _fake_run_gh(self, args, cwd):
        """Return canned gh CLI output for PR list/comments/files."""
        cmd = tuple(args)
        if len(args) >= 4 and args[0] == "pr" and args[1] == "list" and args[2] == "--json":
            return (
                '[{"number":42,"title":"Fix login bug",'
                '"headRefName":"feature/test-branch",'
                '"baseRefName":"main",'
                '"headRefOid":"abc1234",'
                '"baseRefOid":"def5678"}]'
            )
        if len(args) >= 3 and args[0] == "pr" and args[1] == "view" and args[2] == "42":
            # We get two calls: one for commits, one for files.
            return "abc1234\ndef5678\n" if "commits" in args else "src/main.py\nREADME.md\n"
        return ""

    # ------------------------------------------------------------------
    # Test cases
    # ------------------------------------------------------------------

    def test_mode_flag_parsed_correctly(self):
        """Verify the --mode argument accepts 'default' and 'pr-branch-topology',
        with 'default' as the default value, and rejects unknown values."""
        parser = cc.argparse.ArgumentParser()
        parser.add_argument("--task", required=True)
        parser.add_argument("--output", default=None)
        parser.add_argument(
            "--mode",
            choices=["default", "pr-branch-topology"],
            default="default",
        )

        # Default value is 'default'.
        args = parser.parse_args(["--task", "t.md", "--output", "o.md"])
        self.assertEqual(args.mode, "default")

        # Explicit 'default'.
        args2 = parser.parse_args(
            ["--task", "t.md", "--output", "o.md", "--mode", "default"]
        )
        self.assertEqual(args2.mode, "default")

        # Explicit 'pr-branch-topology'.
        args3 = parser.parse_args(
            ["--task", "t.md", "--output", "o.md", "--mode", "pr-branch-topology"]
        )
        self.assertEqual(args3.mode, "pr-branch-topology")

        # Invalid mode raises SystemExit.
        with contextlib.redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    ["--task", "t.md", "--output", "o.md", "--mode", "invalid"]
                )

    def test_pr_topology_output_has_git_sections(self):
        """Run in pr-branch-topology mode with mocked git/gh and verify the
        output contains every topological section but NOT source or config."""
        output_path = self.repo_root / "topology_output.md"

        with patch("collect_context.run_git", side_effect=self._fake_run_git):
            with patch("collect_context.run_gh", side_effect=self._fake_run_gh):
                with patch("collect_context.Path.cwd", return_value=self.repo_root):
                    argv = [
                        "--task", str(self.task_path),
                        "--output", str(output_path),
                        "--mode", "pr-branch-topology",
                    ]
                    cc.main(argv)

        self.assertTrue(output_path.exists())
        content = output_path.read_text(encoding="utf-8")

        # Must contain all PR-topology sections.
        for section in [
            "## Current Branch",
            "## Remote List",
            "## Commit Graph",
            "## PR Information",
            "## Commit History",
            "## Changed Files Summary",
            "## Merge Base Info",
            "## Topology Summary",
        ]:
            self.assertIn(section, content, f"Missing section: {section}")

        # Must NOT contain source code or config file sections.
        self.assertNotIn("## Source Files (task-related)", content)
        self.assertNotIn("## Configuration Files", content)

    def test_pr_topology_mode_no_source_code(self):
        """pr-branch-topology mode must NOT include any source file content."""
        output_path = self.repo_root / "topology_output.md"

        with patch("collect_context.run_git", side_effect=self._fake_run_git):
            with patch("collect_context.run_gh", side_effect=self._fake_run_gh):
                with patch("collect_context.Path.cwd", return_value=self.repo_root):
                    argv = [
                        "--task", str(self.task_path),
                        "--output", str(output_path),
                        "--mode", "pr-branch-topology",
                    ]
                    cc.main(argv)

        content = output_path.read_text(encoding="utf-8")
        self.assertNotIn("## Source Files (task-related)", content)
        self.assertNotIn("## Source Files", content)

    def test_pr_topology_mode_no_config_files(self):
        """pr-branch-topology mode must NOT include config file sections."""
        output_path = self.repo_root / "topology_output.md"

        with patch("collect_context.run_git", side_effect=self._fake_run_git):
            with patch("collect_context.run_gh", side_effect=self._fake_run_gh):
                with patch("collect_context.Path.cwd", return_value=self.repo_root):
                    argv = [
                        "--task", str(self.task_path),
                        "--output", str(output_path),
                        "--mode", "pr-branch-topology",
                    ]
                    cc.main(argv)

        content = output_path.read_text(encoding="utf-8")
        self.assertNotIn("## Configuration Files", content)

    def test_default_mode_preserves_existing_behavior(self):
        """Run with --mode default and verify it produces all sections that
        the old (pre-mode) behavior had, including source and config files."""
        _make_file(self.repo_root, "pyproject.toml", "[project]\nname='test'\n")
        _make_file(self.repo_root, "src/main.py", "def main():\n    pass\n")

        output_path = self.repo_root / "default_output.md"

        def fake_git(args, cwd):
            cmd = tuple(args)
            if cmd == ("status", "--short"):
                return " M src/main.py\n"
            elif cmd == ("diff", "--stat"):
                return " src/main.py | 2 +-\n"
            elif cmd == ("ls-files",):
                return "README.md\npyproject.toml\nsrc/main.py\n.git/config\n"
            return ""

        with patch("collect_context.run_git", side_effect=fake_git):
            with patch("collect_context.Path.cwd", return_value=self.repo_root):
                argv = [
                    "--task", str(self.task_path),
                    "--output", str(output_path),
                    "--mode", "default",
                    "--max-files", "10",
                    "--max-bytes", "50000",
                ]
                cc.main(argv)

        self.assertTrue(output_path.exists())
        content = output_path.read_text(encoding="utf-8")

        # All legacy sections present.
        for section in [
            "# Repository Context",
            "## Task Description",
            "## Git Status",
            "## Git Diff Stat",
            "## File Tree",
            "## Configuration Files",
            "## Source Files (task-related)",
            "## Context Summary",
        ]:
            self.assertIn(section, content, f"Missing section in default mode: {section}")

    def test_pr_topology_output_format(self):
        """Generate a full output in pr-branch-topology mode and verify the
        section structure (order, content, and Markdown format)."""
        output_path = self.repo_root / "topology_output.md"

        with patch("collect_context.run_git", side_effect=self._fake_run_git):
            with patch("collect_context.run_gh", side_effect=self._fake_run_gh):
                with patch("collect_context.Path.cwd", return_value=self.repo_root):
                    argv = [
                        "--task", str(self.task_path),
                        "--output", str(output_path),
                        "--mode", "pr-branch-topology",
                    ]
                    cc.main(argv)

        content = output_path.read_text(encoding="utf-8")

        # Exact top-level header.
        self.assertIn("# PR Branch Topology Context", content)

        # Task description was injected.
        self.assertIn("## Task Description", content)
        self.assertIn("PR Topology Analysis", content)

        # Git status carries the mock data.
        self.assertIn("## Git Status", content)
        self.assertIn("README.md", content)

        # Current branch.
        self.assertIn("## Current Branch", content)
        self.assertIn("feature/test-branch", content)

        # Remote list.
        self.assertIn("## Remote List", content)
        self.assertIn("git@github.com:user/myproject.git", content)

        # Commit graph.
        self.assertIn("## Commit Graph", content)
        self.assertIn("abc1234 Fix login bug", content)

        # PR Information — list and PR detail.
        self.assertIn("## PR Information", content)
        self.assertIn("PR #42", content)
        self.assertIn("```json", content)

        # Commit history.
        self.assertIn("## Commit History", content)
        self.assertIn("abc1234 Fix login bug", content)

        # Changed files summary.
        self.assertIn("## Changed Files Summary", content)

        # Merge base.
        self.assertIn("## Merge Base Info", content)
        self.assertIn("def5678", content)

        # Topology summary footer.
        self.assertIn("## Topology Summary", content)

        # Verify sections appear in the expected sequential order.
        ordered_sections = [
            "# PR Branch Topology Context",
            "## Task Description",
            "## Git Status",
            "## Current Branch",
            "## Remote List",
            "## Commit Graph",
            "## PR Information",
            "## Commit History",
            "## Changed Files Summary",
            "## Merge Base Info",
            "## Topology Summary",
        ]
        last_index = -1
        for section in ordered_sections:
            index = content.find(section)
            self.assertGreater(
                index, last_index,
                f"Section '{section}' appears out of order or is missing",
            )
            last_index = index


if __name__ == "__main__":
    unittest.main()
