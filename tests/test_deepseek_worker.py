"""Comprehensive unit tests for scripts/deepseek_worker.py.

Uses only stdlib (unittest, unittest.mock, tempfile, io, pathlib).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the scripts directory is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import deepseek_worker as dw


# ============================================================================
# Helper: reusable diff snippets
# ============================================================================

_VALID_DIFF = """--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,3 @@
 old line
-new line
 unchanged
"""

_VALID_MULTI_DIFF = """--- a/src/a.py
+++ b/src/a.py
@@ -10,5 +10,5 @@
 unchanged
-old
+new
 unchanged

--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,0 +1,5 @@
+def test_new():
+    pass
"""

_VALID_NEW_FILE_DIFF = """--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,3 @@
+print("hello")
+print("world")
"""


def _make_template_for_name(name: str, body: str) -> str:
    """Build a minimal template file content that includes a heading for *name*."""
    return f"## Template: {name}\n{body}\n"


# ============================================================================
#  1  CLI / Argument Parsing
# ============================================================================


class TestCLIArgs(unittest.TestCase):
    """Test that argparse correctly parses all required and optional args."""

    def setUp(self):
        self.parser = dw.create_parser()

    def test_all_required_args_parsed(self):
        argv = [
            "--model", "deepseek-v4-pro",
            "--task", "task.md",
            "--context", ".deepseek-forge/repo_context.md",
            "--output", ".deepseek-forge/patch.diff",
            "--template", "implement_patch",
            "--endpoint", "https://api.deepseek.com/chat/completions",
            "--api-key-env", "DEEPSEEK_API_KEY",
            "--temperature", "0.2",
            "--timeout", "120",
        ]
        args = self.parser.parse_args(argv)
        self.assertEqual(args.model, "deepseek-v4-pro")
        self.assertEqual(args.task, "task.md")
        self.assertEqual(args.template, "implement_patch")
        self.assertEqual(args.endpoint, "https://api.deepseek.com/chat/completions")
        self.assertEqual(args.api_key_env, "DEEPSEEK_API_KEY")
        self.assertAlmostEqual(args.temperature, 0.2)
        self.assertEqual(args.timeout, 120)
        self.assertIsNone(args.failure_log)

    def test_optional_failure_log(self):
        argv = [
            "--model", "m",
            "--task", "t.md",
            "--context", "c.md",
            "--output", "o.diff",
            "--template", "tpl",
            "--endpoint", "https://x",
            "--api-key-env", "K",
            "--temperature", "0",
            "--timeout", "1",
            "--failure-log", ".deepseek-forge/check.log",
        ]
        args = self.parser.parse_args(argv)
        self.assertEqual(args.failure_log, ".deepseek-forge/check.log")

    def test_defaults_applied_when_omitted(self):
        """Verify that optional args get their correct defaults."""
        argv = [
            "--model", "deepseek-v4-pro",
            "--task", "task.md",
            "--context", "context.md",
            "--output", "output.diff",
        ]
        args = self.parser.parse_args(argv)
        self.assertEqual(args.template, "implement_patch")
        self.assertEqual(args.endpoint, "https://api.deepseek.com/chat/completions")
        self.assertEqual(args.api_key_env, "DEEPSEEK_API_KEY")
        self.assertAlmostEqual(args.temperature, 0.2)
        self.assertEqual(args.timeout, 120)
        self.assertIsNone(args.failure_log)

    def test_model_still_required(self):
        """--model has no default and should still be required."""
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args([
                    "--task", "t.md",
                    "--context", "c.md",
                    "--output", "o.diff",
                ])

    def test_missing_required_arg_raises_system_exit(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args(["--model", "test"])

    def test_invalid_temperature_type(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args([
                    "--model", "m",
                    "--task", "t.md",
                    "--context", "c.md",
                    "--output", "o.diff",
                    "--template", "tpl",
                    "--endpoint", "https://x",
                    "--api-key-env", "K",
                    "--temperature", "not-a-float",
                    "--timeout", "1",
                ])

    def test_invalid_timeout_type(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args([
                    "--model", "m",
                    "--task", "t.md",
                    "--context", "c.md",
                    "--output", "o.diff",
                    "--template", "tpl",
                    "--endpoint", "https://x",
                    "--api-key-env", "K",
                    "--temperature", "0.2",
                    "--timeout", "not-an-int",
                ])


# ============================================================================
#  2  read_template
# ============================================================================


class TestReadTemplate(unittest.TestCase):
    """Tests for :func:`dw.read_template`."""

    _TEMPLATES_CONTENT = """# Templates

Some preamble text.

## Template: implement_patch
You are an AI assistant.
Output only unified diffs.
Do not use code fences.

## Template: fix_tests
Fix the failing tests.
Only output unified diffs.

## Template: `review_patch`
Review this patch.
Output JSON.
"""

    _TEMPLATES_WITH_BACKTICKS = """# Templates

## Template: `implement_patch`
You are an AI assistant.
Output only unified diffs.

## Template: fix_tests
Fix the failing tests.

## Template: review_patch
Review this patch.
"""

    def test_extracts_correct_template(self):
        with patch("pathlib.Path.read_text", return_value=self._TEMPLATES_CONTENT):
            result = dw.read_template("/fake/path", "implement_patch")
        self.assertIn("You are an AI assistant", result)
        self.assertIn("Output only unified diffs", result)
        self.assertNotIn("fix_tests", result)
        self.assertNotIn("review_patch", result)

    def test_extracts_middle_template(self):
        with patch("pathlib.Path.read_text", return_value=self._TEMPLATES_CONTENT):
            result = dw.read_template("/fake/path", "fix_tests")
        self.assertIn("Fix the failing tests", result)
        self.assertIn("Only output unified diffs", result)
        self.assertNotIn("implement_patch", result)
        self.assertNotIn("review_patch", result)

    def test_extracts_backtick_quoted_template(self):
        with patch("pathlib.Path.read_text", return_value=self._TEMPLATES_CONTENT):
            result = dw.read_template("/fake/path", "review_patch")
        self.assertIn("Review this patch", result)
        self.assertIn("Output JSON", result)

    def test_template_name_with_backticks_in_file_only(self):
        with patch("pathlib.Path.read_text", return_value=self._TEMPLATES_WITH_BACKTICKS):
            result = dw.read_template("/fake/path", "implement_patch")
        self.assertIn("You are an AI assistant", result)

    def test_template_not_found_raises_value_error(self):
        with patch("pathlib.Path.read_text", return_value=self._TEMPLATES_CONTENT):
            with self.assertRaises(ValueError) as ctx:
                dw.read_template("/fake/path", "nonexistent_template")
        self.assertIn("nonexistent_template", str(ctx.exception))
        self.assertIn("references/prompt_templates.md", str(ctx.exception))

    def test_template_name_case_sensitive(self):
        with patch("pathlib.Path.read_text", return_value=self._TEMPLATES_CONTENT):
            with self.assertRaises(ValueError):
                dw.read_template("/fake/path", "IMPLEMENT_PATCH")

    def test_last_template_reads_to_end(self):
        content = """## Template: first
content one

## Template: last
final content
more lines
"""
        with patch("pathlib.Path.read_text", return_value=content):
            result = dw.read_template("/fake/path", "last")
        self.assertIn("final content", result)
        self.assertIn("more lines", result)

    def test_single_template_reads_to_end(self):
        content = """## Template: only
just this content
"""
        with patch("pathlib.Path.read_text", return_value=content):
            result = dw.read_template("/fake/path", "only")
        self.assertIn("just this content", result)

    def test_heading_level_3_template(self):
        content = """### Template: alt_level
content at level 3
"""
        with patch("pathlib.Path.read_text", return_value=content):
            result = dw.read_template("/fake/path", "alt_level")
        self.assertIn("content at level 3", result)


# ============================================================================
#  3  extract_diff
# ============================================================================


class TestExtractDiff(unittest.TestCase):
    """Tests for :func:`dw.extract_diff`."""

    def test_clean_diff_returned_as_is(self):
        result = dw.extract_diff(_VALID_DIFF)
        self.assertEqual(result, _VALID_DIFF.strip())

    def test_fenced_diff_extracted_with_warning(self):
        fenced = "```diff\n" + _VALID_DIFF.strip() + "\n```"
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(fenced)
        self.assertEqual(result.strip(), _VALID_DIFF.strip())
        self.assertIn("Warning", mock_stderr.getvalue())
        self.assertIn("code fences", mock_stderr.getvalue())

    def test_fenced_without_language_specifier(self):
        fenced = "```\n" + _VALID_DIFF.strip() + "\n```"
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(fenced)
        self.assertEqual(result.strip(), _VALID_DIFF.strip())
        self.assertIn("Warning", mock_stderr.getvalue())

    def test_fenced_no_closing_fence(self):
        fenced = "```diff\n" + _VALID_DIFF.strip()
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(fenced)
        self.assertEqual(result.strip(), _VALID_DIFF.strip())
        self.assertIn("Warning", mock_stderr.getvalue())

    def test_no_diff_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            dw.extract_diff("This is just text, no diff here at all.")
        self.assertIn("valid unified diff", str(ctx.exception))

    def test_empty_response_raises(self):
        with self.assertRaises(ValueError):
            dw.extract_diff("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            dw.extract_diff("   \n  \t  \n")

    def test_fenced_but_no_diff_inside_raises(self):
        fenced = "```\nplain text\n```"
        with self.assertRaises(ValueError) as ctx:
            dw.extract_diff(fenced)
        self.assertIn("valid unified diff", str(ctx.exception))

    def test_multi_file_diff_extracted(self):
        result = dw.extract_diff(_VALID_MULTI_DIFF)
        self.assertIn("--- a/src/a.py", result)
        self.assertIn("--- a/tests/test_a.py", result)

    def test_shell_command_standalone_line_stripped(self):
        """Shell command on its own line after valid diff is stripped as commentary."""
        text = """--- a/file.py
+++ b/file.py
@@ -1 +1 @@
-old
+new
$ rm -rf /"""
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(text)
        self.assertIn("--- a/file.py", result)
        self.assertIn("+new", result)
        self.assertNotIn("$ rm -rf", result)
        self.assertIn("non-diff lines", mock_stderr.getvalue())

    def test_git_command_standalone_line_stripped(self):
        """Git command on its own line after valid diff is stripped as commentary."""
        text = """--- a/file.py
+++ b/file.py
@@ -1 +1 @@
-old
+new
git add file.py"""
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(text)
        self.assertIn("--- a/file.py", result)
        self.assertIn("+new", result)
        self.assertNotIn("git add", result)
        self.assertIn("non-diff lines", mock_stderr.getvalue())

    def test_response_with_shebang_rejected(self):
        # The first line is a shebang -- not a diff header.
        bad = """#!/bin/bash
echo "this is not a diff"
"""
        with self.assertRaises(ValueError):
            dw.extract_diff(bad)

    def test_new_file_diff_accepted(self):
        result = dw.extract_diff(_VALID_NEW_FILE_DIFF)
        self.assertIn("--- /dev/null", result)
        self.assertIn("+++ b/src/new.py", result)

    def test_commentary_before_diff_stripped(self):
        """Text before the first --- line is stripped, warning emitted."""
        text = "Here is the patch:\n\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(text)
        self.assertNotIn("Here is the patch", result)
        self.assertIn("--- a/file.py", result)
        self.assertIn("+new", result)
        self.assertIn("non-diff lines", mock_stderr.getvalue())

    def test_commentary_after_diff_stripped(self):
        """Text after the last diff content line is stripped, warning emitted."""
        text = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n\nDone."
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(text)
        self.assertNotIn("Done.", result)
        self.assertIn("--- a/file.py", result)
        self.assertIn("+new", result)
        self.assertIn("non-diff lines", mock_stderr.getvalue())

    def test_commentary_both_sides_stripped(self):
        """Commentary before AND after the diff is stripped."""
        text = "Here you go:\n\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n\nLet me know if it works."
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(text)
        self.assertNotIn("Here you go", result)
        self.assertNotIn("Let me know", result)
        self.assertIn("--- a/file.py", result)
        self.assertIn("+new", result)
        self.assertIn("non-diff lines", mock_stderr.getvalue())

    def test_all_commentary_no_diff_raises_value_error(self):
        """If the entire response is commentary (no --- line), ValueError is raised."""
        text = "Here is your fix! It should work now."
        with self.assertRaises(ValueError) as ctx:
            dw.extract_diff(text)
        self.assertIn("valid unified diff", str(ctx.exception))

    def test_diff_with_code_fences_and_commentary_stripped(self):
        """Code fences are removed; commentary outside fences is already excluded."""
        text = "Sure, here it is:\n```diff\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n```\nHope that helps!"
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = dw.extract_diff(text)
        self.assertNotIn("Sure, here it is", result)
        self.assertNotIn("Hope that helps", result)
        self.assertIn("--- a/file.py", result)
        self.assertIn("+new", result)
        stderr = mock_stderr.getvalue()
        self.assertIn("code fences", stderr)
        # No "non-diff lines" warning because the fenced content
        # was already a clean unified diff with no commentary inside.


# ============================================================================
#  4  validate_diff
# ============================================================================


class TestValidateDiff(unittest.TestCase):
    """Tests for :func:`dw.validate_diff`."""

    def test_valid_diff_returns_true(self):
        self.assertTrue(dw.validate_diff(_VALID_DIFF))

    def test_multi_file_diff_returns_true(self):
        self.assertTrue(dw.validate_diff(_VALID_MULTI_DIFF))

    def test_new_file_diff_returns_true(self):
        self.assertTrue(dw.validate_diff(_VALID_NEW_FILE_DIFF))

    def test_empty_string_returns_false(self):
        self.assertFalse(dw.validate_diff(""))

    def test_whitespace_only_returns_false(self):
        self.assertFalse(dw.validate_diff("   \n   \n"))

    def test_none_returns_false(self):
        self.assertFalse(dw.validate_diff(None))  # type: ignore[arg-type]

    def test_missing_src_header_returns_false(self):
        bad = "+++ b/file.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_missing_dst_header_returns_false(self):
        bad = "--- a/file.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_missing_hunk_header_returns_false(self):
        bad = "--- a/file.py\n+++ b/file.py\n-old\n+new\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_shell_command_dollar_space_rejected(self):
        # Standalone line (no + nor - prefix) that starts with "$ ".
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n$ echo done\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_shell_command_gt_space_rejected(self):
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n> output.txt\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_bash_start_rejected(self):
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\nbash -c \"echo hi\"\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_bash_exact_rejected(self):
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\nbash\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_shebang_rejected(self):
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n#!/bin/bash\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_git_add_rejected(self):
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\ngit add .\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_git_commit_rejected(self):
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\ngit commit -m x\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_git_push_rejected(self):
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\ngit push origin main\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_git_add_inside_diff_line_rejected(self):
        """Substring check catches git add even inside a diff +/- line."""
        bad = "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@\n context\n-git add file\n+git add file\n"
        self.assertFalse(dw.validate_diff(bad))

    def test_code_containing_bash_word_accepted(self):
        """A diff line that adds code with the word 'bash' should be accepted.
        Only standalone 'bash' lines are shell commands."""
        ok = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n # old comment\n+# use bash to run\n"
        self.assertTrue(dw.validate_diff(ok))

    def test_code_containing_dollar_accepted(self):
        """A diff line with $ inside code (not at line start) should be accepted."""
        ok = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n old\n+print(f'$price')\n"
        self.assertTrue(dw.validate_diff(ok))


# ============================================================================
#  5  build_api_request
# ============================================================================


class TestBuildApiRequest(unittest.TestCase):
    """Tests for :func:`dw.build_api_request`."""

    def test_structures_request_correctly(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user message"},
        ]
        result = dw.build_api_request("my-model", messages, 0.7)
        self.assertEqual(result["model"], "my-model")
        self.assertEqual(result["messages"], messages)
        self.assertAlmostEqual(result["temperature"], 0.7)

    def test_keys_are_present(self):
        result = dw.build_api_request("m", [], 0.0)
        self.assertIn("model", result)
        self.assertIn("messages", result)
        self.assertIn("temperature", result)
        self.assertEqual(len(result), 3)


# ============================================================================
#  6  call_deepseek_api
# ============================================================================


class TestCallDeepSeekApi(unittest.TestCase):
    """Tests for :func:`dw.call_deepseek_api`."""

    _TEST_BODY = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}

    def test_successful_call_returns_parsed_json(self):
        response_payload = {
            "choices": [{"message": {"content": "response text"}}],
        }
        response_bytes = json.dumps(response_payload).encode("utf-8")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = response_bytes
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = dw.call_deepseek_api(
                "https://api.example.com/chat/completions",
                "sk-test-key",
                self._TEST_BODY,
                30,
            )

        self.assertEqual(result["choices"][0]["message"]["content"], "response text")

    def test_request_includes_correct_headers(self):
        response_bytes = json.dumps({"choices": []}).encode("utf-8")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = response_bytes
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            dw.call_deepseek_api(
                "https://api.example.com/chat/completions",
                "sk-secret-12345",
                self._TEST_BODY,
                30,
            )

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertIsInstance(req, urllib.request.Request)
        self.assertEqual(req.headers["Content-type"], "application/json")
        self.assertEqual(req.headers["Authorization"], "Bearer sk-secret-12345")

    def test_request_body_is_correct_json(self):
        response_bytes = json.dumps({"choices": []}).encode("utf-8")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = response_bytes
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            dw.call_deepseek_api(
                "https://api.example.com/chat/completions",
                "sk-key",
                self._TEST_BODY,
                30,
            )

        req = mock_urlopen.call_args[0][0]
        req_body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(req_body, self._TEST_BODY)

    def test_http_error_raised(self):
        error_body = MagicMock()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                "https://api.example.com/chat/completions",
                401,
                "Unauthorized",
                {},
                error_body,
            )

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                dw.call_deepseek_api(
                    "https://api.example.com/chat/completions",
                    "sk-key",
                    self._TEST_BODY,
                    30,
                )
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()

    def test_http_error_400_raised(self):
        error_body = MagicMock()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                "https://api.example.com/chat/completions",
                400,
                "Bad Request",
                {},
                error_body,
            )

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                dw.call_deepseek_api(
                    "https://api.example.com/chat/completions",
                    "sk-key",
                    self._TEST_BODY,
                    30,
                )
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()

    def test_timeout_error_raised(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError(
                socket.timeout("timed out")
            )

            with self.assertRaises(urllib.error.URLError) as ctx:
                dw.call_deepseek_api(
                    "https://api.example.com/chat/completions",
                    "sk-key",
                    self._TEST_BODY,
                    30,
                )
            self.assertIsInstance(ctx.exception.reason, socket.timeout)

    def test_timeout_error_with_timeouterror(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError(
                TimeoutError("timed out")
            )

            with self.assertRaises(urllib.error.URLError) as ctx:
                dw.call_deepseek_api(
                    "https://api.example.com/chat/completions",
                    "sk-key",
                    self._TEST_BODY,
                    30,
                )
            self.assertIsInstance(ctx.exception.reason, TimeoutError)

    def test_network_error_raised(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError(
                OSError("Connection refused")
            )

            with self.assertRaises(urllib.error.URLError) as ctx:
                dw.call_deepseek_api(
                    "https://api.example.com/chat/completions",
                    "sk-key",
                    self._TEST_BODY,
                    30,
                )
            self.assertIn("Connection refused", str(ctx.exception.reason))

    def test_timeout_argument_passed_to_urlopen(self):
        response_bytes = json.dumps({"choices": []}).encode("utf-8")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = response_bytes
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            dw.call_deepseek_api(
                "https://api.example.com/chat/completions",
                "sk-key",
                self._TEST_BODY,
                77,
            )

        mock_urlopen.assert_called_once()
        _, kwargs = mock_urlopen.call_args
        self.assertEqual(kwargs.get("timeout"), 77)


# ============================================================================
#  7  sanitize_error_text
# ============================================================================


class TestSanitizeErrorText(unittest.TestCase):
    """Tests for :func:`dw.sanitize_error_text`."""

    def test_bearer_token_is_redacted(self):
        text = "Header: Authorization: Bearer sk-abc123xyz token\nother text"
        result = dw.sanitize_error_text(text)
        self.assertNotIn("sk-abc123xyz", result)
        self.assertIn("[REDACTED]", result)
        self.assertIn("other text", result)

    def test_multiple_bearer_tokens_are_redacted(self):
        text = (
            "Auth1: Authorization: Bearer key1\n"
            "Auth2: Authorization: Bearer key2\n"
        )
        result = dw.sanitize_error_text(text)
        self.assertNotIn("key1", result)
        self.assertNotIn("key2", result)
        self.assertEqual(result.count("[REDACTED]"), 2)

    def test_no_bearer_token_passes_through(self):
        text = "just some error message\nnothing sensitive"
        result = dw.sanitize_error_text(text)
        self.assertEqual(result, text)

    def test_empty_string(self):
        self.assertEqual(dw.sanitize_error_text(""), "")

    def test_case_insensitive_redaction(self):
        text = "authorization: bearer SK-SECRET value"
        result = dw.sanitize_error_text(text)
        self.assertNotIn("SK-SECRET", result)
        self.assertIn("[REDACTED]", result)


# ============================================================================
#  7b  sanitize_log_content
# ============================================================================


class TestSanitizeLogContent(unittest.TestCase):
    """Tests for :func:`dw.sanitize_log_content`."""

    def test_bearer_token_in_authorization_header_redacted(self):
        text = "Authorization: Bearer sk-abc123xyz token"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("sk-abc123xyz", result)
        self.assertIn("[REDACTED]", result)

    def test_standalone_bearer_redacted(self):
        text = "curl -H 'Bearer abc-def-123' https://api.example.com"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("abc-def-123", result)
        self.assertIn("[REDACTED]", result)

    def test_env_var_key_redacted(self):
        text = "DEEPSEEK_API_KEY=sk-secret-key-here"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("sk-secret-key-here", result)
        self.assertIn("[REDACTED]", result)

    def test_env_var_secret_redacted(self):
        text = "export SECRET=my-hidden-value"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("my-hidden-value", result)
        self.assertIn("SECRET=[REDACTED]", result)

    def test_env_var_token_redacted(self):
        text = "GITHUB_TOKEN=ghp_abc123def456"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("ghp_abc123def456", result)
        self.assertIn("TOKEN=[REDACTED]", result)

    def test_env_var_password_redacted(self):
        text = "PASSWORD=supersecret123"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("supersecret123", result)
        self.assertIn("PASSWORD=[REDACTED]", result)

    def test_env_var_passwd_redacted(self):
        text = "PASSWD=mydbpassword"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("mydbpassword", result)
        self.assertIn("PASSWD=[REDACTED]", result)

    def test_url_credentials_redacted(self):
        text = "Download from https://user:pass@example.com/file"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("user:pass@", result)
        self.assertIn("[REDACTED]", result)

    def test_aws_key_redacted(self):
        text = "AWS key: AKIA1234567890ABCDEF"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("AKIA1234567890ABCDEF", result)
        self.assertIn("AKIA[REDACTED]", result)

    def test_openai_key_redacted(self):
        text = "The API key is sk-proj-deadbeef1234567890 in config"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("sk-proj-deadbeef1234567890", result)
        self.assertIn("sk-[REDACTED]", result)

    def test_github_pat_redacted(self):
        text = "Token is github_pat_11ABCDEFGHIJKLMNO"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("github_pat_11ABCDEFGHIJKLMNO", result)
        self.assertIn("[REDACTED]", result)

    def test_ghp_token_redacted(self):
        text = "Auth with ghp_abcdefghijklmnopqrstuvwxyz1234"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz1234", result)
        self.assertIn("[REDACTED]", result)

    def test_gho_token_redacted(self):
        text = "OAuth token: gho_abcdefghijklmnopqrstuvwxyz1234"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("gho_abcdefghijklmnopqrstuvwxyz1234", result)
        self.assertIn("[REDACTED]", result)

    def test_case_insensitive_key_redaction(self):
        text = "api_key=my-secret-token and API_KEY=another-one"
        result = dw.sanitize_log_content(text)
        self.assertNotIn("my-secret-token", result)
        self.assertNotIn("another-one", result)
        self.assertEqual(result.count("[REDACTED]"), 2)

    def test_no_sensitive_data_passes_through(self):
        text = "Tests passed: 10, failed: 0. All good."
        result = dw.sanitize_log_content(text)
        self.assertEqual(result, text)

    def test_multiple_redactions_in_same_text(self):
        text = (
            "API_KEY=abc123\n"
            "Authorization: Bearer xyz789\n"
            "https://admin:secret@db.example.com/connect\n"
        )
        result = dw.sanitize_log_content(text)
        self.assertNotIn("abc123", result)
        self.assertNotIn("xyz789", result)
        self.assertNotIn("admin:secret@", result)
        self.assertEqual(result.count("[REDACTED]"), 3)

    def test_empty_string(self):
        self.assertEqual(dw.sanitize_log_content(""), "")


# ============================================================================
#  8  main() integration tests
# ============================================================================

_TPL_TPL = "## Template: tpl\nYou are an AI. Output unified diffs only.\n"


class TestMain(unittest.TestCase):
    """Integration-style tests for :func:`dw.main` using mocked dependencies."""

    # ------------------------------------------------------------------
    #  8a  API key missing
    # ------------------------------------------------------------------

    def test_api_key_missing_exits_with_error(self):
        with patch("os.environ.get", return_value=None):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                with self.assertRaises(SystemExit) as cm:
                    dw.main(["--model", "m",
                             "--task", "t.md",
                             "--context", "c.md",
                             "--output", "o.diff",
                             "--template", "tpl",
                             "--endpoint", "https://x",
                             "--api-key-env", "MISSING_KEY",
                             "--temperature", "0",
                             "--timeout", "1"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Environment variable MISSING_KEY not set",
                       mock_stderr.getvalue())

    # ------------------------------------------------------------------
    #  8b  Template not found
    # ------------------------------------------------------------------

    def test_template_not_found_exits(self):
        with patch("os.environ.get", return_value="sk-test"):
            with patch("pathlib.Path.read_text",
                       return_value="## Template: other\ncontent"):
                with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                    with self.assertRaises(SystemExit) as cm:
                        dw.main(["--model", "m",
                                 "--task", "t.md",
                                 "--context", "c.md",
                                 "--output", "o.diff",
                                 "--template", "missing_template",
                                 "--endpoint", "https://x",
                                 "--api-key-env", "KEY",
                                 "--temperature", "0",
                                 "--timeout", "1"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("missing_template", mock_stderr.getvalue())

    # ------------------------------------------------------------------
    #  8c  Successful flow
    # ------------------------------------------------------------------

    def test_full_success_flow(self):
        api_response = {
            "choices": [{"message": {"content": _VALID_DIFF.strip()}}],
        }
        response_bytes = json.dumps(api_response).encode("utf-8")

        read_side_effect = [
            _make_template_for_name("implement_patch", "Be an AI."),
            "# Task\nImplement feature X.",
            "# Context\nFile contents here.",
        ]

        with patch("os.environ.get", return_value="sk-test-key"):
            with patch("pathlib.Path.read_text",
                       side_effect=read_side_effect):
                with patch("pathlib.Path.mkdir") as mock_mkdir:
                    with patch("pathlib.Path.write_text") as mock_write:
                        with patch("urllib.request.urlopen") as mock_urlopen:
                            mock_resp = MagicMock()
                            mock_resp.read.return_value = response_bytes
                            mock_urlopen.return_value.__enter__.return_value = mock_resp

                            with patch("sys.stdout",
                                       new_callable=io.StringIO) as mock_stdout:
                                dw.main(["--model", "deepseek-v4-pro",
                                         "--task", "task.md",
                                         "--context", "context.md",
                                         "--output", "output.diff",
                                         "--template", "implement_patch",
                                         "--endpoint", "https://api.deepseek.com/chat/completions",
                                         "--api-key-env", "DEEPSEEK_API_KEY",
                                         "--temperature", "0.2",
                                         "--timeout", "120"])

        mock_write.assert_called_once()
        written_content = mock_write.call_args[0][0]
        self.assertIn("--- a/", written_content)
        self.assertIn("+++ b/", written_content)

        mock_mkdir.assert_called_once()
        stdout = mock_stdout.getvalue()
        self.assertIn("Patch written to output.diff", stdout)

    # ------------------------------------------------------------------
    #  8d  HTTP error
    # ------------------------------------------------------------------

    def test_http_error_exits(self):
        error_body = MagicMock()
        error_body.read.return_value = b'{"error": "invalid request"}'
        http_err = urllib.error.HTTPError(
            "https://api.example.com/chat/completions",
            400,
            "Bad Request",
            {},
            error_body,
        )

        read_side_effect = [
            _make_template_for_name("tpl", "template body"),
            "task content",
            "context content",
        ]

        with patch("os.environ.get", return_value="sk-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.side_effect = http_err

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit) as cm:
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://api.example.com/chat/completions",
                                     "--api-key-env", "KEY",
                                     "--temperature", "0",
                                     "--timeout", "30"])

        self.assertEqual(cm.exception.code, 1)
        stderr = mock_stderr.getvalue()
        self.assertIn("API returned HTTP 400", stderr)
        self.assertIn("invalid request", stderr)
        http_err.close()

    # ------------------------------------------------------------------
    #  8e  Timeout / network errors
    # ------------------------------------------------------------------

    def test_timeout_exits(self):
        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value="sk-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.side_effect = urllib.error.URLError(
                        socket.timeout("timed out")
                    )

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit) as cm:
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://api.example.com/chat/completions",
                                     "--api-key-env", "KEY",
                                     "--temperature", "0",
                                     "--timeout", "45"])

        self.assertEqual(cm.exception.code, 1)
        stderr = mock_stderr.getvalue()
        self.assertIn("timed out after 45s", stderr)

    def test_timeout_with_timeouterror_exits(self):
        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value="sk-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.side_effect = urllib.error.URLError(
                        TimeoutError("connection timed out")
                    )

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit) as cm:
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://x",
                                     "--api-key-env", "K",
                                     "--temperature", "0",
                                     "--timeout", "60"])

        self.assertEqual(cm.exception.code, 1)
        stderr = mock_stderr.getvalue()
        self.assertIn("timed out after 60s", stderr)

    def test_network_error_exits(self):
        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value="sk-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.side_effect = urllib.error.URLError(
                        OSError("Connection refused")
                    )

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit) as cm:
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://x",
                                     "--api-key-env", "K",
                                     "--temperature", "0",
                                     "--timeout", "10"])

        self.assertEqual(cm.exception.code, 1)
        stderr = mock_stderr.getvalue()
        self.assertIn("Network error", stderr)
        self.assertIn("Connection refused", stderr)

    # ------------------------------------------------------------------
    #  8f  Invalid diff in response
    # ------------------------------------------------------------------

    def test_invalid_diff_response_exits(self):
        api_response = {
            "choices": [{"message": {"content": "Sure, here is your patch:\ngit add -A"}}],
        }
        response_bytes = json.dumps(api_response).encode("utf-8")

        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value="sk-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = response_bytes
                    mock_urlopen.return_value.__enter__.return_value = mock_resp

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit) as cm:
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://x",
                                     "--api-key-env", "K",
                                     "--temperature", "0",
                                     "--timeout", "10"])

        self.assertEqual(cm.exception.code, 1)
        stderr = mock_stderr.getvalue()
        self.assertIn("valid unified diff", stderr)
        self.assertIn("Sure, here is your patch", stderr)

    def test_unexpected_api_response_format_exits(self):
        api_response = {"error": "something went wrong"}
        response_bytes = json.dumps(api_response).encode("utf-8")

        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value="sk-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = response_bytes
                    mock_urlopen.return_value.__enter__.return_value = mock_resp

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit) as cm:
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://x",
                                     "--api-key-env", "K",
                                     "--temperature", "0",
                                     "--timeout", "10"])

        self.assertEqual(cm.exception.code, 1)
        stderr = mock_stderr.getvalue()
        self.assertIn("Unexpected API response format", stderr)

    # ------------------------------------------------------------------
    #  8g  Failure log handling
    # ------------------------------------------------------------------

    def test_failure_log_appended_to_user_message(self):
        api_response = {
            "choices": [{"message": {"content": _VALID_DIFF.strip()}}],
        }
        response_bytes = json.dumps(api_response).encode("utf-8")

        read_side_effect = [
            _make_template_for_name("tpl", "template body"),
            "task content",
            "context content",
            "failure log line 1\nfailure log line 2\n",
        ]

        with patch("os.environ.get", return_value="sk-test-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("pathlib.Path.mkdir"):
                    with patch("pathlib.Path.write_text"):
                        with patch("urllib.request.urlopen") as mock_urlopen:
                            mock_resp = MagicMock()
                            mock_resp.read.return_value = response_bytes
                            mock_urlopen.return_value.__enter__.return_value = mock_resp

                            with patch("sys.stdout", new_callable=io.StringIO):
                                dw.main(["--model", "m",
                                         "--task", "t.md",
                                         "--context", "c.md",
                                         "--output", "o.diff",
                                         "--template", "tpl",
                                         "--endpoint", "https://x",
                                         "--api-key-env", "K",
                                         "--temperature", "0",
                                         "--timeout", "10",
                                         "--failure-log", "fail.log"])

                        mock_urlopen.assert_called_once()
                        req = mock_urlopen.call_args[0][0]
                        req_body = json.loads(req.data.decode("utf-8"))
                        user_content = req_body["messages"][1]["content"]
                        self.assertIn("failure log line 1", user_content)
                        self.assertIn("failure log line 2", user_content)

    def test_failure_log_truncated_to_500_lines(self):
        lines = [f"line {i}" for i in range(600)]
        failure_content = "\n".join(lines)

        api_response = {
            "choices": [{"message": {"content": _VALID_DIFF.strip()}}],
        }
        response_bytes = json.dumps(api_response).encode("utf-8")

        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
            failure_content,
        ]

        with patch("os.environ.get", return_value="sk-key"):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("pathlib.Path.mkdir"):
                    with patch("pathlib.Path.write_text"):
                        with patch("urllib.request.urlopen") as mock_urlopen:
                            mock_resp = MagicMock()
                            mock_resp.read.return_value = response_bytes
                            mock_urlopen.return_value.__enter__.return_value = mock_resp

                            with patch("sys.stderr",
                                       new_callable=io.StringIO) as mock_stderr:
                                with patch("sys.stdout",
                                           new_callable=io.StringIO):
                                    dw.main(["--model", "m",
                                             "--task", "t.md",
                                             "--context", "c.md",
                                             "--output", "o.diff",
                                             "--template", "tpl",
                                             "--endpoint", "https://x",
                                             "--api-key-env", "K",
                                             "--temperature", "0",
                                             "--timeout", "10",
                                             "--failure-log", "fail.log"])

                        req = mock_urlopen.call_args[0][0]
                        req_body = json.loads(req.data.decode("utf-8"))
                        user_content = req_body["messages"][1]["content"]
                        # First 100 lines dropped (only last 500 kept).
                        self.assertNotIn("line 0", user_content)
                        self.assertNotIn("line 99", user_content)
                        self.assertIn("line 100", user_content)
                        self.assertIn("line 599", user_content)

                        stderr = mock_stderr.getvalue()
                        self.assertIn("truncated", stderr.lower())

    # ------------------------------------------------------------------
    #  8h  API key never logged
    # ------------------------------------------------------------------

    def test_api_key_not_in_http_error_output(self):
        api_key = "sk-v1-secret-do-not-leak-abc123xyz"

        error_body = MagicMock()
        error_body.read.return_value = b'{"error": "invalid api key"}'
        http_err = urllib.error.HTTPError(
            "https://api.example.com/chat/completions",
            401,
            "Unauthorized",
            {},
            error_body,
        )

        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value=api_key):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.side_effect = http_err

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit):
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://api.example.com/chat/completions",
                                     "--api-key-env", "KEY",
                                     "--temperature", "0",
                                     "--timeout", "10"])

        stderr = mock_stderr.getvalue()
        self.assertNotIn(api_key, stderr)
        http_err.close()

    def test_api_key_not_in_invalid_diff_error_output(self):
        api_key = "sk-proj-another-secret-key-999"
        api_response = {
            "choices": [{"message": {"content": "Here is code:\ngit commit -m hi"}}],
        }
        response_bytes = json.dumps(api_response).encode("utf-8")

        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value=api_key):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = response_bytes
                    mock_urlopen.return_value.__enter__.return_value = mock_resp

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit):
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://api.example.com/chat/completions",
                                     "--api-key-env", "KEY",
                                     "--temperature", "0",
                                     "--timeout", "10"])

        stderr = mock_stderr.getvalue()
        self.assertNotIn(api_key, stderr)

    def test_api_key_not_in_unexpected_format_error(self):
        api_key = "sk-third-token-000"
        api_response = {"error": "bad gateway"}
        response_bytes = json.dumps(api_response).encode("utf-8")

        read_side_effect = [
            _make_template_for_name("tpl", "body"),
            "task",
            "context",
        ]

        with patch("os.environ.get", return_value=api_key):
            with patch("pathlib.Path.read_text", side_effect=read_side_effect):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = response_bytes
                    mock_urlopen.return_value.__enter__.return_value = mock_resp

                    with patch("sys.stderr",
                               new_callable=io.StringIO) as mock_stderr:
                        with self.assertRaises(SystemExit):
                            dw.main(["--model", "m",
                                     "--task", "t.md",
                                     "--context", "c.md",
                                     "--output", "o.diff",
                                     "--template", "tpl",
                                     "--endpoint", "https://api.example.com/chat/completions",
                                     "--api-key-env", "KEY",
                                     "--temperature", "0",
                                     "--timeout", "10"])

        stderr = mock_stderr.getvalue()
        self.assertNotIn(api_key, stderr)


# ============================================================================
#  Run
# ============================================================================

if __name__ == "__main__":
    unittest.main()
