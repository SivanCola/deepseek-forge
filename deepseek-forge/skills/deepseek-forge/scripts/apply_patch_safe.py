#!/usr/bin/env python3
"""
Safely validate and apply unified diff patches.

Performs a series of safety checks on the patch file before allowing
application, then delegates to git apply for the actual patching.
"""

import argparse
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# Patch file parsing helpers
# ---------------------------------------------------------------------------

def parse_patch_file(patch_path: str) -> str:
    """Read and return the full contents of the patch file."""
    with open(patch_path, 'r') as f:
        return f.read()


def extract_file_paths(patch_content: str) -> list[str]:
    """Extract file paths from unified diff headers.

    Uses the standard ``--- a/<path>`` and ``+++ b/<path>`` markers.
    Paths from both sides are collected (``/dev/null`` is not matched
    because those headers use no ``a/`` nor ``b/`` prefix).
    """
    paths: set[str] = set()
    for line in patch_content.splitlines():
        m = re.match(r'^--- a/(.+)$', line)
        if m:
            paths.add(m.group(1))
        m = re.match(r'^\+\+\+ b/(.+)$', line)
        if m:
            paths.add(m.group(1))
    return sorted(paths)


# ---------------------------------------------------------------------------
# Safety checks -- each returns (passed: bool, error_message: str)
# ---------------------------------------------------------------------------

def check_empty(patch_content: str) -> tuple[bool, str]:
    """Reject if the patch file is empty or contains only whitespace."""
    if not patch_content.strip():
        return False, "Patch file is empty or contains only whitespace"
    return True, ""


def check_absolute_paths(patch_content: str) -> tuple[bool, str]:
    """Reject if any file path in the diff is absolute."""
    for path in extract_file_paths(patch_content):
        if path.startswith('/'):
            return False, f"Absolute path detected in diff: {path}"
    return True, ""


def check_path_traversal(patch_content: str) -> tuple[bool, str]:
    """Reject if any file path contains ``..`` segments."""
    for path in extract_file_paths(patch_content):
        if '..' in path.split('/'):
            return False, f"Path traversal detected in diff: {path}"
    return True, ""


def check_git_dir(patch_content: str) -> tuple[bool, str]:
    """Reject if any file path targets the ``.git`` directory."""
    for path in extract_file_paths(patch_content):
        if path == '.git' or path.startswith('.git/'):
            return False, f"Path targets .git directory: {path}"
    return True, ""


def check_file_deletion(patch_content: str) -> tuple[bool, str]:
    """Reject if the diff contains a file deletion (``+++ /dev/null``)."""
    if re.search(r'^\+\+\+ /dev/null', patch_content, re.MULTILINE):
        return False, "File deletion detected in patch (+++ /dev/null). File deletions are rejected by default."
    return True, ""


def check_shell_injection(patch_content: str) -> tuple[bool, str]:
    """Reject if non-diff lines contain shell-injection patterns.

    A "non-diff line" is a line that does **not** start with one of the
    standard unified-diff prefixes: ``---``, ``+++``, ``@@``, ``+``,
    ``-``, or `` `` (space / context).
    """
    # Patterns that are suspicious on *any* non-diff line
    always_suspicious: list[tuple[str, str]] = [
        (r'#!/bin/',   'shebang'),
        (r'\bbash\s+-c\b', 'bash -c invocation'),
        (r'\beval\s',  'eval command'),
        (r'exec\s*\(', 'exec() call'),
    ]

    # Commands that are suspicious when standalone on a non-diff line
    standalone_commands = ['rm', 'curl', 'wget']

    diff_line_starts = ('---', '+++', '@@', '+', '-', ' ')

    for line in patch_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith(diff_line_starts):
            continue

        # --- this is a non-diff line; inspect it ---
        for pattern, name in always_suspicious:
            if re.search(pattern, line):
                return False, (
                    f"Shell injection detected ({name}): "
                    f"{stripped[:100]}"
                )

        for cmd in standalone_commands:
            if stripped == cmd or stripped.startswith(cmd + ' '):
                return False, (
                    f"Shell injection detected (standalone {cmd} command): "
                    f"{stripped[:100]}"
                )

    return True, ""


def check_valid_diff_format(patch_content: str) -> tuple[bool, str]:
    """Reject if the content does not look like a unified diff."""
    has_from = bool(re.search(r'^--- ', patch_content, re.MULTILINE))
    has_to = bool(re.search(r'^\+\+\+ ', patch_content, re.MULTILINE))
    has_hunk = bool(re.search(r'^@@ ', patch_content, re.MULTILINE))
    if not (has_from and has_to and has_hunk):
        return False, (
            "File does not appear to be a valid unified diff "
            "(missing ---, +++, or @@ markers)"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all_checks(patch_content: str) -> list[str]:
    """Run every safety check and return a list of failure messages.

    If the returned list is empty all checks passed.
    """
    checks: list[tuple[str, callable]] = [
        ("Empty patch",       check_empty),
        ("Absolute paths",    check_absolute_paths),
        ("Path traversal",    check_path_traversal),
        ("Git directory",     check_git_dir),
        ("File deletion",     check_file_deletion),
        ("Shell injection",   check_shell_injection),
        ("Valid diff format", check_valid_diff_format),
    ]

    failures: list[str] = []
    for name, fn in checks:
        ok, msg = fn(patch_content)
        if not ok:
            failures.append(f"{name}: {msg}")
    return failures


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_apply_check(patch_content: str) -> tuple[bool, str]:
    """Run ``git apply --check`` to verify the patch applies cleanly.

    The patch content is piped via *stdin* so no temporary file is needed.
    """
    result = subprocess.run(
        ['git', 'apply', '--check'],
        input=patch_content,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, ""


def git_apply(patch_content: str) -> tuple[bool, str]:
    """Run ``git apply`` to apply the patch.

    The patch content is piped via *stdin* so no temporary file is needed.
    """
    result = subprocess.run(
        ['git', 'apply'],
        input=patch_content,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, ""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
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
    args = parser.parse_args()

    # 1. Read the patch file
    try:
        patch_content = parse_patch_file(args.patch)
    except OSError as exc:
        print(f"ERROR: Cannot read patch file '{args.patch}': {exc}")
        sys.exit(1)

    # 2. Run safety checks
    failures = run_all_checks(patch_content)
    if failures:
        print("Safety checks failed:")
        for f in failures:
            print(f"  ERROR: {f}")
        sys.exit(1)

    # 3. Print target files
    file_paths = extract_file_paths(patch_content)
    print(f"Would apply to: {', '.join(file_paths)}")

    # 4. Dry-run validation via git
    ok, msg = git_apply_check(patch_content)
    if not ok:
        print(f"ERROR: git apply --check failed: {msg}")
        sys.exit(1)

    # 5. Check mode -- stop here
    if args.check:
        print("CHECK PASSED: Patch is safe to apply")
        sys.exit(0)

    # 6. Apply mode -- apply the patch for real
    ok, msg = git_apply(patch_content)
    if not ok:
        print(f"ERROR: git apply failed: {msg}")
        sys.exit(1)

    # 7. Report success
    hunk_count = len(re.findall(r'^@@', patch_content, re.MULTILINE))
    file_count = len(file_paths)
    print(
        f"APPLIED: Patch applied successfully "
        f"({hunk_count} hunks, {file_count} files)"
    )
    sys.exit(0)


if __name__ == '__main__':
    main()
