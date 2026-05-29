"""Unit tests for artifact isolation in forge_config.py.

Tests thread/run subdirectory isolation, env var overrides, and concurrent-session safety.
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

import forge_config


class TestArtifactIsolation(unittest.TestCase):
    """Tests for the new isolated artifact directory layout."""

    def setUp(self):
        self._saved_artifact_dir = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
        self._saved_thread = os.environ.get("CODEX_THREAD_ID")
        self._saved_run = os.environ.get("DEEPSEEK_FORGE_RUN_ID")
        self._saved_repo_local = os.environ.get("DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS")

    def tearDown(self):
        for key, val in [
            ("DEEPSEEK_FORGE_ARTIFACT_DIR", self._saved_artifact_dir),
            ("CODEX_THREAD_ID", self._saved_thread),
            ("DEEPSEEK_FORGE_RUN_ID", self._saved_run),
            ("DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS", self._saved_repo_local),
        ]:
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)

    def test_default_uses_tmp_with_isolation_subdirs(self):
        """Default path includes repo_hash/thread_id/run_id subdirectories."""
        os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)
        os.environ.pop("DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS", None)
        os.environ["CODEX_THREAD_ID"] = "thread-001"
        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "run-001"

        result = forge_config.get_artifact_dir()
        result_str = str(result)
        self.assertIn("deepseek-forge", result_str)
        self.assertIn("thread-001", result_str)
        self.assertIn("run-001", result_str)

    def test_different_thread_ids_produce_different_paths(self):
        """Concurrent Codex threads must not share artifact directories."""
        os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)
        os.environ.pop("DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS", None)
        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "run-001"

        os.environ["CODEX_THREAD_ID"] = "thread-A"
        path_a = forge_config.get_artifact_dir()

        os.environ["CODEX_THREAD_ID"] = "thread-B"
        path_b = forge_config.get_artifact_dir()

        self.assertNotEqual(path_a, path_b)

    def test_different_run_ids_produce_different_paths(self):
        """Different runs within the same thread must be isolated."""
        os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)
        os.environ.pop("DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS", None)
        os.environ["CODEX_THREAD_ID"] = "thread-001"

        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "run-A"
        path_a = forge_config.get_artifact_dir()

        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "run-B"
        path_b = forge_config.get_artifact_dir()

        self.assertNotEqual(path_a, path_b)

    def test_explicit_artifact_dir_still_gets_isolation_subdirs(self):
        """When DEEPSEEK_FORGE_ARTIFACT_DIR is set, isolation subdirs are appended."""
        os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = "/tmp/custom-artifacts"
        os.environ["CODEX_THREAD_ID"] = "thread-001"
        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "run-001"

        result = forge_config.get_artifact_dir()
        result_str = str(result)
        self.assertIn("custom-artifacts", result_str)
        self.assertIn("thread-001", result_str)
        self.assertIn("run-001", result_str)

    def test_repo_local_mode(self):
        """DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS=true uses .deepseek-forge/."""
        os.environ["DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS"] = "true"
        os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)
        os.environ["CODEX_THREAD_ID"] = "thread-001"
        os.environ["DEEPSEEK_FORGE_RUN_ID"] = "run-001"

        result = forge_config.get_artifact_dir()
        result_str = str(result)
        self.assertIn(".deepseek-forge", result_str)
        self.assertIn("thread-001", result_str)
        self.assertIn("run-001", result_str)

    def test_ensure_artifact_dir_creates_directory(self):
        """ensure_artifact_dir creates the nested path."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = str(tmpdir)
            os.environ["CODEX_THREAD_ID"] = "thread-test"
            os.environ["DEEPSEEK_FORGE_RUN_ID"] = "run-test"
            result = forge_config.ensure_artifact_dir()
            self.assertTrue(result.is_dir())
            self.assertIn("thread-test", str(result))


class TestConfigEnvVars(unittest.TestCase):
    """Tests for new loop/parallelism config env vars."""

    def setUp(self):
        self._saved_max_loops = os.environ.get("DEEPSEEK_FORGE_MAX_LOOPS")
        self._saved_max_parallel = os.environ.get("DEEPSEEK_FORGE_MAX_PARALLEL_AGENTS")

    def tearDown(self):
        for key, val in [
            ("DEEPSEEK_FORGE_MAX_LOOPS", self._saved_max_loops),
            ("DEEPSEEK_FORGE_MAX_PARALLEL_AGENTS", self._saved_max_parallel),
        ]:
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)

    def test_default_max_loops(self):
        os.environ.pop("DEEPSEEK_FORGE_MAX_LOOPS", None)
        self.assertEqual(forge_config.get_max_loops(), 5)

    def test_custom_max_loops(self):
        os.environ["DEEPSEEK_FORGE_MAX_LOOPS"] = "3"
        self.assertEqual(forge_config.get_max_loops(), 3)

    def test_invalid_max_loops_falls_back(self):
        os.environ["DEEPSEEK_FORGE_MAX_LOOPS"] = "invalid"
        self.assertEqual(forge_config.get_max_loops(), 5)

    def test_max_loops_minimum_1(self):
        os.environ["DEEPSEEK_FORGE_MAX_LOOPS"] = "0"
        self.assertEqual(forge_config.get_max_loops(), 1)

    def test_default_max_parallel_agents(self):
        os.environ.pop("DEEPSEEK_FORGE_MAX_PARALLEL_AGENTS", None)
        self.assertEqual(forge_config.get_max_parallel_agents(), 3)

    def test_custom_max_parallel_agents(self):
        os.environ["DEEPSEEK_FORGE_MAX_PARALLEL_AGENTS"] = "5"
        self.assertEqual(forge_config.get_max_parallel_agents(), 5)

    def test_max_parallel_agents_capped_at_8(self):
        os.environ["DEEPSEEK_FORGE_MAX_PARALLEL_AGENTS"] = "10"
        self.assertEqual(forge_config.get_max_parallel_agents(), 8)


if __name__ == "__main__":
    unittest.main()
