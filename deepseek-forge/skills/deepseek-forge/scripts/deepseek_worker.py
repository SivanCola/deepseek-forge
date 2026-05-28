#!/usr/bin/env python3
"""DeepSeek Worker: Call the DeepSeek API to generate patches.

Reads task and repository context, builds a prompt using a named template from
references/prompt_templates.md, calls the DeepSeek chat completions API, and
writes a validated unified diff patch to the output file.

Usage:
    python3 scripts/deepseek_worker.py \
        --model deepseek-v4-pro \
        --task task.md \
        --context .deepseek-forge/repo_context.md \
        --output .deepseek-forge/patch.diff \
        --template implement_patch \
        --endpoint https://api.deepseek.com/chat/completions \
        --api-key-env DEEPSEEK_API_KEY \
        --temperature 0.2 \
        --timeout 120 \
        [--failure-log .deepseek-forge/check.log]

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

# ---------------------------------------------------------------------------
# Project paths (resolved relative to this script's location)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _PROJECT_ROOT / "references" / "prompt_templates.md"


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
        required=True,
        help="Path where the generated patch will be written",
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Name of the template to use from references/prompt_templates.md",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="DeepSeek API endpoint URL",
    )
    parser.add_argument(
        "--api-key-env",
        required=True,
        help="Name of the environment variable holding the API key",
    )
    parser.add_argument(
        "--temperature",
        required=True,
        type=float,
        help="Temperature for the API request",
    )
    parser.add_argument(
        "--timeout",
        required=True,
        type=int,
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
    * Validates the result via :func:`validate_diff`.

    Returns the diff string on success.
    Raises :class:`ValueError` if no valid diff is found.
    """
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
            result = "\n".join(lines[start_idx + 1 : end_idx]).strip()
        else:
            result = "\n".join(lines[start_idx + 1 :]).strip()
    else:
        result = response_text.strip()

    if not validate_diff(result):
        raise ValueError("Response does not contain a valid unified diff")

    return result


def validate_diff(diff_text: str) -> bool:
    """Return ``True`` if *diff_text* is a valid, safe unified diff.

    Checks performed:

    * Not empty.
    * Contains ``--- a/`` and ``+++ b/`` file headers.
    * Contains ``@@ ... @@`` hunk headers.
    * Does **not** contain shell commands (``$ `` / ``> `` / ``bash`` / ``#!/bin/``).
    * Does **not** contain git commands (``git add`` / ``git commit`` / ``git push``).
    """
    if not diff_text or not diff_text.strip():
        return False

    lines = diff_text.splitlines()

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = create_parser()
    args = parser.parse_args(argv)

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
