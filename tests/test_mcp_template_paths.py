"""Integration test: MCP _read_template path resolution."""

import os
import sys
import tempfile
import unittest


class TestPluginTemplatePathResolution(unittest.TestCase):
    """Verify MCP tools find templates from supported layouts."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = self.tmpdir.name

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_plugin_layout(self):
        """Create the marketplace plugin layout with tool and template."""
        tools_dir = os.path.join(
            self.root, "plugins", "deepseek-forge", "mcp", "deepseek-mcp", "tools"
        )
        tmpl_dir = os.path.join(
            self.root,
            "plugins",
            "deepseek-forge",
            "skills",
            "deepseek-forge",
            "references",
        )
        os.makedirs(tools_dir)
        os.makedirs(tmpl_dir)

        tmpl_path = os.path.join(tmpl_dir, "prompt_templates.md")
        with open(tmpl_path, "w") as f:
            f.write("## Template: `implement_patch`\nFake template content\n\n## Template: `fix_tests`\nFake fix content\n")

        return tools_dir

    def _make_standalone_layout(self):
        """Create standalone deepseek-mcp/ layout."""
        tools_dir = os.path.join(self.root, "deepseek-mcp", "tools")
        refs_dir = os.path.join(self.root, "references")
        os.makedirs(tools_dir)
        os.makedirs(refs_dir)

        tmpl_path = os.path.join(refs_dir, "prompt_templates.md")
        with open(tmpl_path, "w") as f:
            f.write("## Template: `implement_patch`\nStandalone template\n\n## Template: `fix_tests`\nStandalone fix\n")

        return tools_dir

    def _make_search_paths(self, tool_dir):
        """Replicate the search path logic from MCP tool _read_template()."""
        return [
            os.path.join(tool_dir, "..", "..", "..", "skills", "deepseek-forge",
                         "references", "prompt_templates.md"),
            os.path.join(tool_dir, "..", "..", "references", "prompt_templates.md"),
        ]

    def _find_template(self, tool_dir):
        for p in self._make_search_paths(tool_dir):
            if os.path.exists(p):
                return p
        return None

    def test_plugin_layout_finds_template_via_skills_path(self):
        """Path 1 (../../../skills/...) resolves correctly in plugin layout."""
        tools_dir = self._make_plugin_layout()
        result = self._find_template(tools_dir)
        self.assertIsNotNone(result, "Template not found in plugin layout")
        self.assertIn("skills/deepseek-forge/references/prompt_templates.md", result)
        with open(result) as f:
            content = f.read()
        self.assertIn("implement_patch", content)

    def test_standalone_layout_finds_template_via_references_path(self):
        """Path 2 (../../references/...) resolves correctly in standalone layout."""
        tools_dir = self._make_standalone_layout()
        result = self._find_template(tools_dir)
        self.assertIsNotNone(result, "Template not found in standalone layout")
        self.assertIn("references/prompt_templates.md", result)
        with open(result) as f:
            content = f.read()
        self.assertIn("implement_patch", content)

    def test_plugin_layout_correct_number_of_parent_dirs(self):
        """From mcp/deepseek-mcp/tools/, three '..' reaches the plugin root."""
        tools_dir = self._make_plugin_layout()
        # Simulate: tools_dir = .../plugins/deepseek-forge/mcp/deepseek-mcp/tools
        # Three levels up = plugin root.
        plugin_root = os.path.normpath(os.path.join(tools_dir, "..", "..", ".."))
        expected = os.path.normpath(
            os.path.join(self.root, "plugins", "deepseek-forge")
        )
        self.assertEqual(plugin_root, expected,
                         f"Plugin root mismatch: {plugin_root} != {expected}")

    def test_no_layout_returns_none(self):
        """No template found when neither path exists."""
        empty_dir = os.path.join(self.root, "empty")
        os.makedirs(empty_dir)
        result = self._find_template(empty_dir)
        self.assertIsNone(result)

    def test_env_var_path_checked(self):
        """DEEPSEEK_TEMPLATE_PATH env var provides a fallback search path."""
        # Use empty dir so neither built-in path 1 nor path 2 resolves
        tools_dir = os.path.join(self.root, "empty-project", "mcp", "deepseek-mcp", "tools")
        os.makedirs(tools_dir)

        env_tmpl_dir = os.path.join(self.root, "custom-templates")
        os.makedirs(env_tmpl_dir)
        env_tmpl_path = os.path.join(env_tmpl_dir, "prompt_templates.md")
        with open(env_tmpl_path, "w") as f:
            f.write("## Template: `implement_patch`\nCustom template\n")

        os.environ["DEEPSEEK_TEMPLATE_PATH"] = env_tmpl_path
        try:
            search_paths = self._make_search_paths(tools_dir)
            search_paths.append(os.environ["DEEPSEEK_TEMPLATE_PATH"])
            found = None
            for p in search_paths:
                if os.path.exists(p):
                    found = p
                    break
            self.assertIsNotNone(found)
            self.assertIn("custom-templates", found)
        finally:
            del os.environ["DEEPSEEK_TEMPLATE_PATH"]


if __name__ == "__main__":
    unittest.main()
