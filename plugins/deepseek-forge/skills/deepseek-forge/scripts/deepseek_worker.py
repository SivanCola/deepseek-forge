#!/usr/bin/env python3
"""DeepSeek Worker: Call the DeepSeek API to generate patches.

Reads task and repository context, builds a prompt using a named template from
references/prompt_templates.md, calls the DeepSeek chat completions API, and
writes a validated unified diff patch to the output file.

Environment variables:
    ``DEEPSEEK_FORGE_HOME``
        Override the root directory where SKILL.md and
        ``references/prompt_templates.md`` are located.  Defaults to the
        parent directory of this script.

Usage:
    python3 scripts/deepseek_worker.py \\
        --model deepseek-v4-pro \\
        --task task.md \\
        --context .deepseek-forge/repo_context.md \\
        --template implement_patch \\
        --endpoint https://api.deepseek.com/chat/completions \\
        --api-key-env DEEPSEEK_API_KEY \\
        --temperature 0.2 \\
        --timeout 120 \\
        [--output .deepseek-forge/patch.diff] \\
        [--failure-log .deepseek-forge/check.log]

    ``--output`` is optional.  The default is ``{artifact_dir}/patch.diff``
    (or ``{artifact_dir}/fix.patch.diff`` when ``--template fix_tests``),
    where the default artifact directory can include ``DEEPSEEK_FORGE_SESSION_ID``.
    where ``artifact_dir`` is resolved via :func:`forge_config.get_artifact_dir`.

    After extracting the patch, the script runs lightweight
    :func:`_validate_output_completeness` checks (warnings only, never blocking)
    to help downstream Codex semantic review by flagging missing file references,
    cosmetic-only diffs, and test-file coverage gaps.

Uses only stdlib -- no external dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

from forge_config import get_artifact_dir, get_forge_home

# ---------------------------------------------------------------------------
# Project paths (resolved via DEEPSEEK_FORGE_HOME, with script-location fallback)
# ---------------------------------------------------------------------------

_SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _resolve_template_path() -> Path:
    """Locate ``references/prompt_templates.md``.

    Prefer ``DEEPSEEK_FORGE_HOME`` if set, otherwise fall back to the
    directory containing this script's parent.
    """
    forge_home = get_forge_home()
    path = forge_home / "references" / "prompt_templates.md"
    if path.is_file():
        return path
    # Fallback: script-relative location.
    fallback = _SCRIPT_PROJECT_ROOT / "references" / "prompt_templates.md"
    return fallback


_TEMPLATE_PATH = _resolve_template_path()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def create_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the worker script."""
    parser = argparse.ArgumentParser(
        description="Call DeepSeek API to generate patches from task and context files."
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model name (e.g., deepseek-v4-pro)",
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Path to the task description file",
    )
    parser.add_argument(
        "--context",
        required=True,
        help="Path to the repository context file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path where the generated patch will be written "
             "(default: {artifact_dir}/patch.diff, or "
             "{artifact_dir}/fix.patch.diff when --template fix_tests).",
    )
    parser.add_argument(
        "--template",
        default="implement_patch",
        help="Name of the template to use from references/prompt_templates.md",
    )
    parser.add_argument(
        "--endpoint",
        default="https://api.deepseek.com/chat/completions",
        help="DeepSeek API endpoint URL",
    )
    parser.add_argument(
        "--api-key-env",
        default="DEEPSEEK_API_KEY",
        help="Name of the environment variable holding the API key",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Temperature for the API request",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Request timeout in seconds",
    )
    parser.add_argument(
        "--failure-log",
        default=None,
        help="Optional path to a failure log file (appended to the user message)",
    )
    return parser


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


def read_template(template_path: str, template_name: str) -> str:
    """Read *template_path* and extract the section named *template_name*.

    The file is expected to contain sections delimited by headings matching
    the pattern ``## Template: <name>`` (backtick-quoted names are also
    recognized).  Everything from the matching heading line to the next
    ``## Template:`` heading (or end-of-file) is returned.
    """
    content = Path(template_path).read_text(encoding="utf-8", errors="replace")

    # Build a regex that matches a heading like:
    #   ## Template: implement_patch
    #   ## Template: `implement_patch`
    #   ### Template: implement_patch
    # ...accepting optional backticks around the name.
    escaped_name = re.escape(template_name)
    heading_pat = re.compile(
        r"^#{2,4}\s+Template:\s+`?" + escaped_name + r"`?",
        re.MULTILINE,
    )

    match = heading_pat.search(content)
    if match is None:
        raise ValueError(
            f"Template '{template_name}' not found in references/prompt_templates.md"
        )

    # Content starts on the line after the heading.
    start = match.end()
    # Move past the newline character(s).
    if start < len(content) and content[start] == "\n":
        start += 1
    elif start + 1 < len(content) and content[start : start + 2] == "\r\n":
        start += 2

    # Find the next template heading (any heading level 2-4).
    next_heading_pat = re.compile(r"^#{2,4}\s+Template:", re.MULTILINE)
    next_match = next_heading_pat.search(content, start)

    if next_match is not None:
        end = next_match.start()
    else:
        end = len(content)

    return content[start:end].strip()


# ---------------------------------------------------------------------------
# Diff extraction & validation
# ---------------------------------------------------------------------------


def extract_diff(response_text: str) -> str:
    """Extract a validated unified diff from the model's response.

    * Strips markdown code fences if present (emits a warning to stderr).
    * Finds the actual diff boundaries (first ``--- `` line through last valid diff
      content line) and strips non-diff commentary before/after the diff.
    * Validates the result via :func:`validate_diff`.

    Returns the diff string on success.
    Raises :class:`ValueError` if no valid diff is found.
    """
    # --- Step 1: remove markdown code fences --------------------------------
    lines = response_text.splitlines()
    start_idx: int | None = None
    end_idx: int | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") and start_idx is None:
            start_idx = i
        elif stripped == "```" and start_idx is not None:
            end_idx = i
            break

    if start_idx is not None:
        print(
            "Warning: Detected markdown code fences, extracting diff content",
            file=sys.stderr,
        )
        if end_idx is not None:
            raw = "\n".join(lines[start_idx + 1 : end_idx]).strip()
        else:
            raw = "\n".join(lines[start_idx + 1 :]).strip()
    else:
        raw = response_text.strip()

    raw_lines = raw.splitlines()

    # --- Step 2: find first diff header line --------------------------------
    first_diff_idx: int | None = None
    for i, line in enumerate(raw_lines):
        if line.startswith("--- "):
            first_diff_idx = i
            break

    if first_diff_idx is None:
        raise ValueError("Response does not contain a valid unified diff")

    # --- Step 3: find last valid diff content line (scan backwards) ---------
    # Valid unified-diff lines start with +, -, space, @, \\, or are blank.
    def _is_diff_line(line: str) -> bool:
        if not line:
            return True
        return line[0] in ("+", "-", " ", "@", "\\")

    last_diff_idx = first_diff_idx
    for i in range(len(raw_lines) - 1, first_diff_idx - 1, -1):
        if _is_diff_line(raw_lines[i]):
            last_diff_idx = i
            break

    # --- Step 4: warn about stripped commentary -----------------------------
    non_diff_before = first_diff_idx
    non_diff_after = len(raw_lines) - last_diff_idx - 1
    total_stripped = non_diff_before + non_diff_after

    if total_stripped > 0:
        print(
            f"Warning: stripped {total_stripped} non-diff lines from response",
            file=sys.stderr,
        )

    # --- Step 5: extract only the diff portion ------------------------------
    diff_lines = raw_lines[first_diff_idx : last_diff_idx + 1]
    result = "\n".join(diff_lines).strip()

    # --- Step 6: validate ---------------------------------------------------
    if not validate_diff(result):
        raise ValueError("Response does not contain a valid unified diff")

    return result


def validate_diff(diff_text: str) -> bool:
    """Return ``True`` if *diff_text* is a valid, safe unified diff.

    Checks performed:

    * Not empty.
    * **Starts with** ``--- `` (first non-blank line is a diff file header).
    * Contains ``--- a/`` or ``--- /dev/null`` and ``+++ b/`` file headers.
    * Contains ``@@ ... @@`` hunk headers.
    * Does **not** contain shell commands (``$ `` / ``> `` / ``bash`` / ``#!/bin/``).
    * Does **not** contain git commands (``git add`` / ``git commit`` / ``git push``).
    """
    if not diff_text or not diff_text.strip():
        return False

    lines = diff_text.splitlines()

    # Must start with a diff header (first non-blank line starts with --- ).
    first_non_blank: str | None = None
    for line in lines:
        if line.strip():
            first_non_blank = line
            break
    if first_non_blank is None or not first_non_blank.startswith("--- "):
        return False

    # Must have at least one source and destination header.
    # Accept both "--- a/path" and "--- /dev/null" (new files).
    has_src = any(
        line.startswith("--- a/") or line.startswith("--- /dev/null")
        for line in lines
    )
    has_dst = any(line.startswith("+++ b/") for line in lines)
    if not (has_src and has_dst):
        return False

    # Must have at least one hunk header.
    if not any("@@ " in line for line in lines):
        return False

    # Must NOT contain shell commands.
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("$ ") or stripped.startswith("> "):
            return False
        if stripped == "bash" or stripped.startswith("bash "):
            return False
        if stripped.startswith("#!/bin/"):
            return False

    # Must NOT contain git commands (case-insensitive check on the full text).
    lower = diff_text.lower()
    if "git add" in lower or "git commit" in lower or "git push" in lower:
        return False

    return True


def _validate_output_completeness(content: str, template: str) -> list[str]:
    """Run lightweight completeness checks on the generated patch.

    Returns a list of warning strings (never blocks -- flagging only).
    Warnings help downstream Codex semantic review by pointing out gaps
    in the generated output.

    For ``implement_patch``:
        * Checks for at least one ``--- a/`` or ``--- /dev/null`` file header.
        * Warns if the diff appears to be cosmetic-only (all +/- lines differ
          only in whitespace).
        * Checks for at least one ``+`` line (not just deletions).

    For ``fix_tests``:
        * Checks that at least one file modified is a test file (``test_*``
          or ``*_test.*``).
        * Warns if only non-test files are touched (may indicate wrong fix
          strategy).
    """
    warnings: list[str] = []

    if not content or not content.strip():
        warnings.append("Output is empty or whitespace-only")
        return warnings

    lines = content.splitlines()

    # --- Extract file paths from diff headers ---------------------------
    # Match both "--- a/path" and "--- /dev/null" (new files), plus "+++ b/path".
    src_paths: list[str] = []
    dst_paths: list[str] = []
    for line in lines:
        if line.startswith("--- a/") or line.startswith("--- /dev/null"):
            # Strip the prefix: "--- a/" or "--- /dev/null"
            if line.startswith("--- /dev/null"):
                src_paths.append("/dev/null")
            else:
                src_paths.append(line[6:])  # len("--- a/") == 6
        elif line.startswith("+++ b/"):
            dst_paths.append(line[6:])  # len("+++ b/") == 6

    all_paths = [p for p in src_paths if p != "/dev/null"] + dst_paths

    # --- Common checks --------------------------------------------------
    # Extract lines that are actual content changes (+, -, not headers).
    plus_lines = [
        l for l in lines
        if l.startswith("+") and not l.startswith("+++ ")
    ]
    minus_lines = [
        l for l in lines
        if l.startswith("-") and not l.startswith("--- ")
    ]

    plus_content = [l[1:] for l in plus_lines]
    minus_content = [l[1:] for l in minus_lines]

    has_plus_line = len(plus_lines) > 0

    # --- Template-specific checks ---------------------------------------
    if template == "implement_patch":
        # Check: has at least one file header.
        if not src_paths:
            warnings.append(
                "Diff contains no file headers "
                "(--check for source files)"
            )
        if not dst_paths:
            warnings.append(
                "Diff contains no destination file headers "
                "(--check for modified files)"
            )

        # Check: has at least one + line (actual additions).
        if not has_plus_line:
            warnings.append(
                "Diff has no '+' lines "
                "(--code changes may be missing)"
            )

        # Check: not purely cosmetic (all +/- lines differ only in whitespace).
        if plus_content and minus_content:
            # Collect all changed content lines, strip whitespace.
            changed_set = set()
            for pc in plus_content:
                changed_set.add(pc.strip())
            for mc in minus_content:
                changed_set.add(mc.strip())
            # If after stripping, all changed lines are identical,
            # only whitespace differs -> cosmetic.
            if len(changed_set) == 1:
                warnings.append(
                    "Diff appears to be cosmetic-only "
                    "(all +/- lines differ only in whitespace)"
                )

    elif template == "fix_tests":
        # Check: at least one file path looks like a test file.
        test_file_patterns = re.compile(r"(?:^|/)test_[^/]+|(?:^|/)[^/]+_test\.[^/]+$")

        test_files = sorted(set(p for p in all_paths if test_file_patterns.search(p)))
        non_test_files = sorted(set(p for p in all_paths if not test_file_patterns.search(p)))

        if not test_files:
            if non_test_files:
                warnings.append(
                    f"Template is fix_tests but only non-test files "
                    f"are modified: {', '.join(non_test_files)}. "
                    f"(--verify that the fix targets the right files)"
                )
            else:
                warnings.append(
                    "Template is fix_tests but no test file paths "
                    "were detected in the diff"
                )
        elif non_test_files:
            warnings.append(
                f"Template is fix_tests but non-test files are also "
                f"modified: {', '.join(non_test_files)}. "
                f"(--verify that non-test changes are intentional)"
            )

    return warnings


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def build_api_request(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> dict:
    """Build the JSON-serialisable request body for the DeepSeek API."""
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }


def call_deepseek_api(
    endpoint: str,
    api_key: str,
    request_body: dict,
    timeout: int,
) -> dict:
    """POST *request_body* to the DeepSeek chat completions *endpoint*.

    Returns the parsed JSON response dict.

    Raises:
        urllib.error.HTTPError: On HTTP 4xx/5xx responses.
        urllib.error.URLError: On network-level errors (DNS, timeout, etc.).
    """
    data = json.dumps(request_body).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sanitize_error_text(text: str) -> str:
    """Redact sensitive values (API keys, auth tokens) from *text*."""
    # Redact "Authorization: Bearer <token>" if it appears anywhere.
    text = re.sub(
        r"Authorization:\s*Bearer\s+\S+",
        "Authorization: Bearer [REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def sanitize_log_content(text: str) -> str:
    """Redact sensitive information from log content before sending to the LLM.

    Redacts: Bearer tokens, API keys, environment variables with secrets,
    URL credentials, and common key patterns (AWS, GitHub, OpenAI).
    """
    # 1. Redact "Authorization: Bearer <token>" and standalone Bearer tokens.
    text = re.sub(
        r"Authorization:\s*Bearer\s+\S+",
        "Authorization: Bearer [REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bBearer\s+\S+", "Bearer [REDACTED]", text)

    # 2. Redact KEY=value, SECRET=value, TOKEN=value, PASSWORD=value,
    #    PASSWD=value (case-insensitive).  Matches standalone keywords or
    #    keywords embedded in longer names (e.g. api_key=, GITHUB_TOKEN=).
    text = re.sub(
        r"(?i)([\w]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD))\s*=\s*\S+",
        r"\1=[REDACTED]",
        text,
    )

    # 3. Redact URL credentials: ://user:pass@
    text = re.sub(r"://[^@\s]+@", "://[REDACTED]@", text)

    # 4. Redact common key patterns: AWS AKIA..., OpenAI sk-..., GitHub tokens.
    text = re.sub(r"\bAKIA[A-Z0-9]{16}\b", "AKIA[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9]+\b", "sk-[REDACTED]", text)
    text = re.sub(r"\bghp_[A-Za-z0-9]+\b", "ghp_[REDACTED]", text)
    text = re.sub(r"\bgho_[A-Za-z0-9]+\b", "gho_[REDACTED]", text)
    text = re.sub(r"\bgithub_pat_[A-Za-z0-9_]+\b", "github_pat_[REDACTED]", text)

    return text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = create_parser()
    args = parser.parse_args(argv)

    # --- 0. Resolve default output path ----------------------------------
    if args.output is None:
        artifact_dir = get_artifact_dir()
        filename = (
            "fix.patch.diff" if args.template == "fix_tests" else "patch.diff"
        )
        args.output = str(artifact_dir / filename)

    # --- 1. Read API key from environment --------------------------------
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(
            f"Error: Environment variable {args.api_key_env} not set",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- 2. Read template ------------------------------------------------
    try:
        template = read_template(str(_TEMPLATE_PATH), args.template)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- 3. Read task and context files ----------------------------------
    task_content = Path(args.task).read_text(encoding="utf-8", errors="replace")
    context_content = Path(args.context).read_text(encoding="utf-8", errors="replace")

    user_parts: list[str] = [task_content, context_content]

    # --- 4. Optionally append failure log --------------------------------
    if args.failure_log:
        failure_log = Path(args.failure_log).read_text(encoding="utf-8", errors="replace")
        failure_lines = failure_log.splitlines()
        if len(failure_lines) > 500:
            failure_log = "\n".join(failure_lines[-500:])
            print(
                "Warning: Failure log truncated to last 500 lines",
                file=sys.stderr,
            )
        failure_log = sanitize_log_content(failure_log)
        user_parts.append(failure_log)

    user_message = "\n\n".join(user_parts)

    # --- 5. Build messages array -----------------------------------------
    messages = [
        {"role": "system", "content": template},
        {"role": "user", "content": user_message},
    ]

    # --- 6. Build and send API request -----------------------------------
    request_body = build_api_request(args.model, messages, args.temperature)

    try:
        response = call_deepseek_api(
            args.endpoint, api_key, request_body, args.timeout
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Error: API returned HTTP {exc.code}", file=sys.stderr)
        print(sanitize_error_text(body), file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            print(
                f"Error: Request timed out after {args.timeout}s",
                file=sys.stderr,
            )
        else:
            print(f"Error: Network error: {reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- 7. Extract assistant response content ---------------------------
    try:
        content: str = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        print(
            f"Error: Unexpected API response format: {exc}",
            file=sys.stderr,
        )
        print(sanitize_error_text(json.dumps(response, indent=2)), file=sys.stderr)
        sys.exit(1)

    # --- 8. Extract and validate the diff --------------------------------
    try:
        patch = extract_diff(content)
    except ValueError:
        print(
            "Error: Response does not contain a valid unified diff",
            file=sys.stderr,
        )
        print(sanitize_error_text(content), file=sys.stderr)
        sys.exit(1)

    # --- 8.5 Validate output completeness ------------------------------
    completeness_warnings = _validate_output_completeness(patch, args.template)
    for w in completeness_warnings:
        print(f"[deepseek-forge] WARNING: {w}", file=sys.stderr)

    # --- 9. Write patch to output file -----------------------------------
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(patch, encoding="utf-8")

    # --- 10. Print summary -----------------------------------------------
    line_count = len(patch.splitlines())
    byte_count = len(patch.encode("utf-8"))
    print(f"Patch written to {args.output} ({line_count} lines, {byte_count} bytes)")


if __name__ == "__main__":
    main()
