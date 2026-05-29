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
        / "plugins"
        / "deepseek-forge"
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
        self._saved = {
            "DEEPSEEK_FORGE_ARTIFACT_DIR": os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR"),
            "DEEPSEEK_FORGE_SESSION_ID": os.environ.get("DEEPSEEK_FORGE_SESSION_ID"),
        }

    def tearDown(self) -> None:
        for var, value in self._saved.items():
            if value is not None:
                os.environ[var] = value
            else:
                os.environ.pop(var, None)

    def test_default_uses_temp_with_artifact_prefix(self) -> None:
        """Without the env var, uses /tmp/deepseek-forge/ with isolation subdirs."""
        os.environ.pop("DEEPSEEK_FORGE_ARTIFACT_DIR", None)
        os.environ.pop("DEEPSEEK_FORGE_SESSION_ID", None)
        result = forge_config.get_artifact_dir()
        self.assertIn("deepseek-forge", str(result))
        self.assertTrue(result.is_absolute())

    def test_env_var_overrides_base_but_appends_isolation_subdirs(self) -> None:
        """DEEPSEEK_FORGE_ARTIFACT_DIR env var sets the base, isolation subdirs are appended."""
        if "DEEPSEEK_FORGE_RUN_ID" not in os.environ:
            os.environ["DEEPSEEK_FORGE_RUN_ID"] = "test-run"
        os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = "/tmp/custom-artifacts"
        os.environ["DEEPSEEK_FORGE_SESSION_ID"] = "ignored-session"
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
        if "DEEPSEEK_FORGE_RUN_ID" not in os.environ:
            os.environ["DEEPSEEK_FORGE_RUN_ID"] = "test-run-create"
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "nonexistent"
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = str(artifact_dir)
            result = forge_config.ensure_artifact_dir()
            # Path.resolve() can resolve /var -> /private/var on macOS.
            self.assertIn("nonexistent", str(result))
            self.assertTrue(result.is_dir())

    def test_noop_when_directory_exists(self) -> None:
        """ensure_artifact_dir is a no-op when the directory already exists."""
        if "DEEPSEEK_FORGE_RUN_ID" not in os.environ:
            os.environ["DEEPSEEK_FORGE_RUN_ID"] = "test-run-noop"
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = tmpdir
            result = forge_config.ensure_artifact_dir()
            self.assertTrue(result.is_dir())
            # The artifact dir now appends isolation subdirs, so the parent chain
            # should contain the tmpdir component.
            self.assertIn(Path(tmpdir).name, [p.name for p in result.parents])

    def test_returns_path_instance(self) -> None:
        """The return value is a pathlib.Path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = tmpdir
            result = forge_config.ensure_artifact_dir()
            self.assertIsInstance(result, Path)


class TestRepoLock(unittest.TestCase):
    """Tests for the per-repository lock helpers."""

    def setUp(self) -> None:
        self._saved = {
            "DEEPSEEK_FORGE_LOCK_PATH": os.environ.get("DEEPSEEK_FORGE_LOCK_PATH"),
            "DEEPSEEK_FORGE_DISABLE_REPO_LOCK": os.environ.get("DEEPSEEK_FORGE_DISABLE_REPO_LOCK"),
            "DEEPSEEK_FORGE_SESSION_ID": os.environ.get("DEEPSEEK_FORGE_SESSION_ID"),
        }
        for var in self._saved:
            os.environ.pop(var, None)

    def tearDown(self) -> None:
        for var, value in self._saved.items():
            if value is not None:
                os.environ[var] = value
            else:
                os.environ.pop(var, None)

    def test_lock_path_falls_back_outside_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = forge_config.get_repo_lock_path(tmpdir)
            self.assertEqual(
                result,
                Path(tmpdir).resolve() / ".deepseek-forge" / "deepseek-forge.lock",
            )

    def test_lock_path_env_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "custom.lock"
            os.environ["DEEPSEEK_FORGE_LOCK_PATH"] = str(lock_path)
            self.assertEqual(forge_config.get_repo_lock_path(tmpdir), lock_path.resolve())

    def test_repo_lock_creates_owner_and_removes_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["DEEPSEEK_FORGE_SESSION_ID"] = "session-A"
            lock_path = forge_config.get_repo_lock_path(tmpdir)
            with forge_config.repo_lock(tmpdir, reason="unit-test") as held:
                self.assertEqual(held, lock_path)
                self.assertTrue(lock_path.is_dir())
                owner = (lock_path / "owner").read_text(encoding="utf-8")
                self.assertIn("session=session-A", owner)
                self.assertIn("reason=unit-test", owner)
            self.assertFalse(lock_path.exists())

    def test_repo_lock_rejects_concurrent_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with forge_config.repo_lock(tmpdir, reason="first"):
                with self.assertRaises(RuntimeError) as ctx:
                    with forge_config.repo_lock(tmpdir, reason="second"):
                        pass
                self.assertIn("already held", str(ctx.exception))

    def test_repo_lock_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["DEEPSEEK_FORGE_DISABLE_REPO_LOCK"] = "1"
            lock_path = forge_config.get_repo_lock_path(tmpdir)
            with forge_config.repo_lock(tmpdir, reason="disabled") as held:
                self.assertIsNone(held)
            self.assertFalse(lock_path.exists())


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

    def test_apply_patch_safe_imports_repo_lock(self) -> None:
        """apply_patch_safe.py imports repo_lock for apply-mode concurrency safety."""
        import apply_patch_safe
        self.assertTrue(hasattr(apply_patch_safe, "repo_lock"),
                        "apply_patch_safe should import repo_lock")


if __name__ == "__main__":
    unittest.main()
