"""Unit tests for scripts/forge_config.py.

Uses only stdlib (unittest, tempfile, pathlib, os).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the skill scripts directory is importable.
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "skills"
        / "deepseek-forge"
        / "scripts"
    ),
)

import forge_config


class TestGetForgeHome(unittest.TestCase):
    """Tests for :func:`forge_config.get_forge_home`."""

    def setUp(self) -> None:
        self._saved = os.environ.get("DEEPSEEK_FORGE_HOME")

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["DEEPSEEK_FORGE_HOME"] = self._saved
        else:
            os.environ.pop("DEEPSEEK_FORGE_HOME", None)

    def test_default_returns_parent_of_scripts_dir(self) -> None:
        """Without the env var, get_forge_home returns the parent of scripts/."""
        os.environ.pop("DEEPSEEK_FORGE_HOME", None)
        result = forge_config.get_forge_home()
        self.assertTrue(result.is_dir(),
                        f"Expected existing directory, got {result}")
        self.assertIn("deepseek-forge", str(result))

    def test_env_var_overrides_default(self) -> None:
        """DEEPSEEK_FORGE_HOME env var takes priority over the default."""
        os.environ["DEEPSEEK_FORGE_HOME"] = "/tmp/test-forge-home"
        result = forge_config.get_forge_home()
        # Path.resolve() can resolve /tmp -> /private/tmp on macOS.
        self.assertIn("test-forge-home", str(result))


class TestGetArtifactDir(unittest.TestCase):
    """Tests for :func:`forge_config.get_artifact_dir`."""

    def setUp(self) -> None:
        self._saved = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = self._saved
        else:
            os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)

    def test_default_uses_temp_with_pid(self) -> None:
        """Without the env var, uses /tmp/deepseek-forge-{pid}."""
        os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)
        result = forge_config.get_artifact_dir()
        self.assertIn(f"deepseek-forge-{os.getpid()}", str(result))
        self.assertTrue(result.is_absolute())

    def test_env_var_overrides_default(self) -> None:
        """DEEPSEEK_FORGE_ARTIFACT_DIR env var takes priority."""
        os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = "/tmp/custom-artifacts"
        result = forge_config.get_artifact_dir()
        # Path.resolve() can resolve /tmp -> /private/tmp on macOS.
        self.assertIn("custom-artifacts", str(result))


class TestEnsureArtifactDir(unittest.TestCase):
    """Tests for :func:`forge_config.ensure_artifact_dir`."""

    def setUp(self) -> None:
        self._saved = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = self._saved
        else:
            os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)

    def test_creates_directory_if_not_exists(self) -> None:
        """ensure_artifact_dir creates the directory when it does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "nonexistent" / "subdir"
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = str(artifact_dir)
            result = forge_config.ensure_artifact_dir()
            # Path.resolve() can resolve /var -> /private/var on macOS.
            self.assertIn("nonexistent", str(result))
            self.assertIn("subdir", str(result))
            self.assertTrue(result.is_dir())

    def test_noop_when_directory_exists(self) -> None:
        """ensure_artifact_dir is a no-op when the directory already exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = tmpdir
            result = forge_config.ensure_artifact_dir()
            self.assertTrue(result.is_dir())
            # The resolved path should end with the temp dir basename.
            self.assertEqual(result.name, Path(tmpdir).name)

    def test_returns_path_instance(self) -> None:
        """The return value is a pathlib.Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = tmpdir
            result = forge_config.ensure_artifact_dir()
            self.assertIsInstance(result, Path)


class TestIntegrationImport(unittest.TestCase):
    """Integration: verify that scripts can import and use forge_config."""

    def test_collect_context_imports_forge_config(self) -> None:
        """collect_context.py imports get_artifact_dir from forge_config."""
        import collect_context
        self.assertTrue(hasattr(collect_context, "get_artifact_dir"),
                        "collect_context should import get_artifact_dir")

    def test_deepseek_worker_imports_forge_config(self) -> None:
        """deepseek_worker.py imports get_forge_home from forge_config."""
        import deepseek_worker
        self.assertTrue(hasattr(deepseek_worker, "get_forge_home"),
                        "deepseek_worker should import get_forge_home")

    def test_apply_patch_safe_does_not_depend_on_forge_config(self) -> None:
        """apply_patch_safe.py should NOT import forge_config (it takes --patch directly)."""
        import apply_patch_safe
        self.assertFalse(hasattr(apply_patch_safe, "get_forge_home"),
                         "apply_patch_safe should not import get_forge_home")
        self.assertFalse(hasattr(apply_patch_safe, "get_artifact_dir"),
                         "apply_patch_safe should not import get_artifact_dir")


if __name__ == "__main__":
    unittest.main()
