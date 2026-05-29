"""Tests for the local Codex marketplace layout."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestMarketplaceLayout(unittest.TestCase):
    """Validate the repository layout expected by Codex plugin CLI."""

    def test_marketplace_points_to_plugin_root(self) -> None:
        marketplace_path = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
        data = json.loads(marketplace_path.read_text(encoding="utf-8"))

        plugin = next(
            item for item in data["plugins"] if item["name"] == "deepseek-forge"
        )
        source_path = plugin["source"]["path"]
        self.assertEqual(source_path, "./plugins/deepseek-forge")

        plugin_root = (REPO_ROOT / source_path).resolve()
        self.assertTrue((plugin_root / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((plugin_root / ".mcp.json").is_file())
        self.assertTrue(
            (plugin_root / "skills" / "deepseek-forge" / "SKILL.md").is_file()
        )
        self.assertTrue(
            (plugin_root / "mcp" / "deepseek-mcp" / "server.py").is_file()
        )

    def test_repository_root_is_marketplace_not_plugin_root(self) -> None:
        self.assertFalse((REPO_ROOT / ".codex-plugin").exists())
        self.assertFalse((REPO_ROOT / ".mcp.json").exists())


if __name__ == "__main__":
    unittest.main()
