"""Tests for shared configuration module."""

import os
import sys
import unittest

# Add MCP server root to path so we can import the tools package.
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "plugins",
        "deepseek-forge",
        "mcp",
        "deepseek-mcp",
    ),
)


class TestConfigReading(unittest.TestCase):
    """Tests for config.get_config() — env var reading and defaults."""

    def setUp(self):
        self._saved = {}
        for var in (
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_MODEL",
            "DEEPSEEK_REASONING_EFFORT",
            "DEEPSEEK_ENABLE_1M_CONTEXT",
            "DEEPSEEK_FORGE_ARTIFACT_DIR",
            "DEEPSEEK_FORGE_SESSION_ID",
        ):
            self._saved[var] = os.environ.get(var)
            os.environ.pop(var, None)

    def tearDown(self):
        for var, val in self._saved.items():
            if val is not None:
                os.environ[var] = val
            else:
                os.environ.pop(var, None)

    def _import_config(self):
        from tools import config
        return config

    def test_api_key_missing_raises(self):
        config = self._import_config()
        with self.assertRaises(RuntimeError) as ctx:
            config.get_config()
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))

    def test_model_default(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["model"], "deepseek-v4-pro")

    def test_model_can_be_overridden(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-chat"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["model"], "deepseek-chat")

    def test_reasoning_effort_default_max(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["reasoning_effort"], "max")

    def test_reasoning_effort_can_be_overridden(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_REASONING_EFFORT"] = "high"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["reasoning_effort"], "high")

    def test_reasoning_effort_low_maps_to_high(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_REASONING_EFFORT"] = "low"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["reasoning_effort"], "high")

    def test_reasoning_effort_medium_maps_to_high(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_REASONING_EFFORT"] = "medium"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["reasoning_effort"], "high")

    def test_reasoning_effort_xhigh_maps_to_max(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_REASONING_EFFORT"] = "xhigh"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["reasoning_effort"], "max")

    def test_reasoning_effort_invalid_value_raises(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_REASONING_EFFORT"] = "turbo"
        config = self._import_config()
        with self.assertRaises(ValueError) as ctx:
            config.get_config()
        self.assertIn("turbo", str(ctx.exception))
        self.assertIn("DEEPSEEK_REASONING_EFFORT", str(ctx.exception))

    def test_enable_1m_context_default_true(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        config = self._import_config()
        cfg = config.get_config()
        self.assertTrue(cfg["enable_1m_context"])

    def test_enable_1m_context_false_string(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_ENABLE_1M_CONTEXT"] = "false"
        config = self._import_config()
        cfg = config.get_config()
        self.assertFalse(cfg["enable_1m_context"])

    def test_enable_1m_context_zero(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_ENABLE_1M_CONTEXT"] = "0"
        config = self._import_config()
        cfg = config.get_config()
        self.assertFalse(cfg["enable_1m_context"])

    def test_enable_1m_context_one(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        os.environ["DEEPSEEK_ENABLE_1M_CONTEXT"] = "1"
        config = self._import_config()
        cfg = config.get_config()
        self.assertTrue(cfg["enable_1m_context"])

    def test_endpoint_has_default(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        config = self._import_config()
        cfg = config.get_config()
        self.assertTrue(cfg["endpoint"].startswith("https://"))
        self.assertIn("deepseek", cfg["endpoint"])

    def test_temperature_has_default(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["temperature"], 0.2)

    def test_timeout_has_default(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        config = self._import_config()
        cfg = config.get_config()
        self.assertEqual(cfg["timeout"], 120)

    def test_artifact_dir_default_uses_tmp_base(self):
        config = self._import_config()
        path = str(config.get_artifact_dir())
        self.assertIn("/tmp/deepseek-forge/", path)
        # Should include repo_hash/thread_id/run_id isolation subdirs
        parts = path.split("/")
        self.assertGreater(len(parts), 4, f"path too shallow: {path}")

    def test_artifact_dir_uses_session_id_in_thread(self):
        os.environ["DEEPSEEK_FORGE_SESSION_ID"] = "chat-beta-2"
        config = self._import_config()
        path = str(config.get_artifact_dir())
        self.assertIn("/chat-beta-2/", path)

    def test_artifact_path_uses_artifact_dir_override(self):
        os.environ["DEEPSEEK_FORGE_ARTIFACT_DIR"] = "/tmp/deepseek-custom"
        config = self._import_config()
        artifact_path = config.get_artifact_path("patch.diff")
        self.assertIn("deepseek-custom", artifact_path)
        self.assertTrue(
            artifact_path.endswith("/patch.diff"),
            f"expected .../patch.diff got {artifact_path}"
        )


class TestBuildRequestBody(unittest.TestCase):
    """Tests for config.build_request_body()."""

    def test_includes_model_and_messages(self):
        from tools import config
        body = config.build_request_body("deepseek-v4-pro", [{"role": "user", "content": "hi"}])
        self.assertEqual(body["model"], "deepseek-v4-pro")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])

    def test_includes_temperature(self):
        from tools import config
        body = config.build_request_body("deepseek-v4-pro", [])
        self.assertIn("temperature", body)
        self.assertEqual(body["temperature"], 0.2)

    def test_includes_reasoning_effort_when_provided(self):
        from tools import config
        body = config.build_request_body(
            "deepseek-v4-pro", [], reasoning_effort="max"
        )
        self.assertIn("reasoning_effort", body)
        self.assertEqual(body["reasoning_effort"], "max")

    def test_no_reasoning_effort_when_none(self):
        from tools import config
        body = config.build_request_body("deepseek-v4-pro", [], reasoning_effort=None)
        self.assertNotIn("reasoning_effort", body)

    def test_no_reasoning_effort_when_empty_string(self):
        from tools import config
        body = config.build_request_body("deepseek-v4-pro", [], reasoning_effort="")
        self.assertNotIn("reasoning_effort", body)

    def test_reasoning_effort_included_for_v4_pro_model(self):
        from tools import config
        body = config.build_request_body(
            "deepseek-v4-pro", [], reasoning_effort="max"
        )
        self.assertIn("reasoning_effort", body)

    def test_reasoning_effort_included_for_reasoner_model(self):
        from tools import config
        body = config.build_request_body(
            "deepseek-reasoner", [], reasoning_effort="high"
        )
        self.assertIn("reasoning_effort", body)

    def test_reasoning_effort_omitted_for_chat_model(self):
        from tools import config
        body = config.build_request_body(
            "deepseek-chat", [], reasoning_effort="max"
        )
        self.assertNotIn("reasoning_effort", body)


class TestReadTemplate(unittest.TestCase):
    """Tests for config.read_template() using DEEPSEEK_TEMPLATE_PATH override."""

    def setUp(self):
        self._saved_path = os.environ.get("DEEPSEEK_TEMPLATE_PATH")

    def tearDown(self):
        if self._saved_path is not None:
            os.environ["DEEPSEEK_TEMPLATE_PATH"] = self._saved_path
        else:
            os.environ.pop("DEEPSEEK_TEMPLATE_PATH", None)

    def test_reads_real_template_from_default_search_path(self):
        """read_template finds implement_patch from the real prompt_templates.md."""
        from tools import config
        result = config.read_template("implement_patch")
        self.assertIn("unified diff", result.lower())

    def test_env_var_override_takes_priority(self):
        """DEEPSEEK_TEMPLATE_PATH should override the default template path."""
        tmpdir = __import__("tempfile").mkdtemp()
        try:
            tmpl_path = os.path.join(tmpdir, "prompt_templates.md")
            with open(tmpl_path, "w") as f:
                f.write("## Template: `implement_patch`\nCustom override content\n")

            os.environ["DEEPSEEK_TEMPLATE_PATH"] = tmpl_path
            from tools import config
            import importlib
            importlib.reload(config)
            result = config.read_template("implement_patch")
            self.assertEqual(result, "Custom override content")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_unknown_template_raises(self):
        from tools import config
        with self.assertRaises(ValueError):
            config.read_template("nonexistent_template_xyz")


if __name__ == "__main__":
    unittest.main()
