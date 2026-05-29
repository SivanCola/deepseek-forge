"""Smoke tests for the Codex plugin package layout."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestPluginPackage(unittest.TestCase):
    """Verify local marketplace packaging and reinstall behavior."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.plugin_dir = cls.repo_root / "plugins" / "deepseek-forge"
        cls.plugin_json = cls.plugin_dir / ".codex-plugin" / "plugin.json"
        cls.check_script = cls.repo_root / "scripts" / "check-plugin-package.sh"
        cls.reinstall_script = cls.repo_root / "scripts" / "reinstall-local-plugin.sh"

    def _remove_plugin_bytecode(self):
        for path in list(self.plugin_dir.rglob("__pycache__")):
            shutil.rmtree(path)
        for path in list(self.plugin_dir.rglob("*.pyc")):
            path.unlink()

    def _plugin_version(self):
        with open(self.plugin_json, encoding="utf-8") as f:
            return json.load(f)["version"]

    def test_check_plugin_package_script_passes_for_clean_package(self):
        """The package preflight should pass after bytecode cleanup."""
        self._remove_plugin_bytecode()
        result = subprocess.run(
            ["bash", str(self.check_script)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("codex"), "codex CLI is not installed")
    def test_reinstall_script_installs_clean_cache(self):
        """Reinstall script cleans source bytecode and installs a clean cache copy."""
        sentinel_dir = (
            self.plugin_dir
            / "skills"
            / "deepseek-forge"
            / "scripts"
            / "__pycache__"
        )
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        sentinel = sentinel_dir / "sentinel.pyc"
        sentinel.write_bytes(b"bytecode")

        with tempfile.TemporaryDirectory() as codex_home:
            env = os.environ.copy()
            env["CODEX_HOME"] = codex_home
            result = subprocess.run(
                ["bash", str(self.reinstall_script)],
                cwd=self.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(sentinel.exists(), "source .pyc sentinel was not cleaned")

            version = self._plugin_version()
            cache_root = (
                Path(codex_home)
                / "plugins"
                / "cache"
                / "deepseek-forge"
                / "deepseek-forge"
                / version
            )
            self.assertTrue((cache_root / ".codex-plugin" / "plugin.json").is_file())
            self.assertTrue((cache_root / ".mcp.json").is_file())
            self.assertTrue((cache_root / "skills" / "deepseek-forge" / "SKILL.md").is_file())

            artifacts = [
                str(path.relative_to(cache_root))
                for path in cache_root.rglob("*")
                if path.name == "__pycache__" or path.suffix == ".pyc"
            ]
            self.assertEqual(artifacts, [])


if __name__ == "__main__":
    unittest.main()
