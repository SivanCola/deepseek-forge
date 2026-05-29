"""Tool: deepseek.fix_tests — Generate fix patch for failing tests via DeepSeek API."""

import os
import re
import sys

from . import config

FIX_TESTS_SCHEMA = {
    "description": "Generate a fix patch for failing tests using DeepSeek",
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The original task description",
            },
            "context": {
                "type": "string",
                "description": "Repository context",
            },
            "failure_log": {
                "type": "string",
                "description": "Content of the failure log from run_checks.sh",
            },
            "output": {
                "type": "string",
                "description": "Path to write the fix patch. Defaults to the deepseek-forge artifact directory.",
            },
        },
        "required": ["task", "context", "failure_log"],
    },
}


def _extract_diff(response_text: str) -> str:
    text = response_text.strip()

    if text.startswith("```diff"):
        print(
            "[deepseek-forge-mcp] Warning: removing diff code fences from response",
            file=sys.stderr,
        )
        text = text[len("```diff"):].strip()
    elif text.startswith("```"):
        print(
            "[deepseek-forge-mcp] Warning: removing code fences from response",
            file=sys.stderr,
        )
        text = text[len("```"):].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    lines = text.split("\n")

    # Find diff boundaries
    diff_start = None
    diff_end = None

    diff_prefixes = ("+", "-", " ", "@@", "---", "+++", "\\")

    for i, line in enumerate(lines):
        if diff_start is None and (
            line.startswith("--- a/") or line.startswith("--- /dev/null")
        ):
            diff_start = i
        if line and any(line.startswith(p) for p in diff_prefixes):
            diff_end = i

    if diff_start is None or diff_end is None or diff_end < diff_start:
        raise ValueError("Response contains no valid unified diff")

    stripped_before = diff_start
    stripped_after = len(lines) - 1 - diff_end
    total_stripped = stripped_before + stripped_after

    if total_stripped > 0:
        print(
            f"[deepseek-forge-mcp] Warning: stripped {total_stripped} non-diff lines from response",
            file=sys.stderr,
        )

    diff_text = "\n".join(lines[diff_start:diff_end + 1])
    _validate_diff(diff_text)
    return diff_text


def _validate_diff(diff_text: str):
    if not diff_text or not diff_text.strip():
        raise ValueError("Response contains no content (empty)")

    has_src = diff_text.strip().startswith("--- ")
    has_dst = "+++ b/" in diff_text
    has_hunk = "@@" in diff_text and " @@" in diff_text

    if not has_src:
        raise ValueError("Response does not start with '--- ' source header")
    if not has_dst:
        raise ValueError("Response missing '+++ b/' destination header")
    if not has_hunk:
        raise ValueError("Response missing '@@ ... @@' hunk header")

    for line in diff_text.split("\n"):
        stripped = line.strip()
        if stripped in ("$ ", "$", "> ", "bash", "#!/bin/bash", "#!/bin/sh"):
            raise ValueError(
                f"Response contains prohibited shell content: '{stripped}'"
            )
        if "git add" in stripped or "git commit" in stripped or "git push" in stripped:
            raise ValueError(f"Response contains prohibited git command: '{stripped}'")


def _sanitize_log_content(text: str) -> str:
    """Redact sensitive information from log content before sending to API."""
    # Redact Bearer tokens
    text = re.sub(r'Authorization:\s*Bearer\s+\S+', 'Authorization: Bearer [REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'Bearer\s+[\w\-\.]+', 'Bearer [REDACTED]', text)

    # Redact API keys and secrets (key=value patterns)
    text = re.sub(r'(?i)(\w*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD)\w*)\s*[=:]\s*\S+', r'\1=[REDACTED]', text)

    # Redact URL credentials
    text = re.sub(r'://[^:@]+:[^@]+@', '://[REDACTED]:[REDACTED]@', text)

    # Redact common key patterns
    text = re.sub(r'\b(AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9]{22,})\b', '[REDACTED]', text)

    return text


def handle_fix_tests(arguments: dict) -> dict:
    cfg = config.get_config()
    task = arguments["task"]
    context = arguments["context"]
    failure_log = arguments["failure_log"]
    output_path = arguments.get("output") or config.get_artifact_path(
        "fix.patch.diff"
    )

    # Sanitize sensitive data before sending to API
    failure_log = _sanitize_log_content(failure_log)

    system_prompt = config.read_template("fix_tests")

    failure_lines = failure_log.split("\n")
    if len(failure_lines) > 500:
        failure_log = "\n".join(failure_lines[-500:])
        failure_log = f"[truncated to last 500 lines]\n\n{failure_log}"

    user_content = (
        f"# Original Task\n\n{task}\n\n"
        f"# Repository Context\n\n{context}\n\n"
        f"# Failure Log\n\n{failure_log}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    request_body = config.build_request_body(
        cfg["model"], messages, cfg["reasoning_effort"]
    )
    raw_response = config.call_api(
        cfg["endpoint"], cfg["api_key"], request_body, cfg["timeout"]
    )
    diff_text = _extract_diff(raw_response)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(diff_text)

    lines = diff_text.count("\n") + 1
    return {
        "patch_path": output_path,
        "patch_size": len(diff_text),
        "lines": lines,
        "warnings": [],
    }
