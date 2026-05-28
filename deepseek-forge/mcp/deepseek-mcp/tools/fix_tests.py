"""Tool: deepseek.fix_tests — Generate fix patch for failing tests via DeepSeek API."""

import json
import os
import re
import sys
import urllib.request

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
                "description": "Path to write the fix patch",
                "default": ".deepseek-forge/fix.patch.diff",
            },
        },
        "required": ["task", "context", "failure_log"],
    },
}


def _read_template(template_name: str) -> str:
    tool_dir = os.path.dirname(__file__)

    # Build search paths in priority order
    search_paths = [
        os.path.join(tool_dir, "..", "..", "..", "skills", "deepseek-forge", "references", "prompt_templates.md"),
        os.path.join(tool_dir, "..", "..", "references", "prompt_templates.md"),
    ]

    # Add env var path if set
    env_path = os.environ.get("DEEPSEEK_TEMPLATE_PATH")
    if env_path:
        search_paths.append(env_path)

    tmpl_path = None
    for p in search_paths:
        if os.path.exists(p):
            tmpl_path = p
            break

    if tmpl_path is None:
        raise FileNotFoundError(
            f"Template file not found. Searched: {', '.join(search_paths)}"
        )

    content = open(tmpl_path).read()
    marker = f"## Template: `{template_name}`"
    alt_marker = f"## Template: {template_name}"

    start = content.find(marker)
    if start == -1:
        start = content.find(alt_marker)
    if start == -1:
        raise ValueError(f"Template '{template_name}' not found in {tmpl_path}")

    body_start = content.find("\n", start) + 1
    remainder = content[body_start:]
    next_section = remainder.find("\n## Template:")
    if next_section != -1:
        return remainder[:next_section].strip()
    return remainder.strip()


def _extract_diff(response_text: str) -> str:
    text = response_text.strip()

    if text.startswith("```diff"):
        print(
            "[deepseek-mcp] Warning: removing diff code fences from response",
            file=sys.stderr,
        )
        text = text[len("```diff"):].strip()
    elif text.startswith("```"):
        print(
            "[deepseek-mcp] Warning: removing code fences from response",
            file=sys.stderr,
        )
        text = text[len("```"):].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    lines = text.split("\n")

    # Find diff boundaries
    diff_start = None
    diff_end = None

    diff_prefixes = ("+", "-", " ", "@@", "---", "+++")

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
            f"[deepseek-mcp] Warning: stripped {total_stripped} non-diff lines from response",
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


def _call_api(
    endpoint: str,
    api_key: str,
    model: str,
    messages: list,
    temperature: float,
    timeout: int,
) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"DeepSeek API HTTP {e.code}: {body[:500]}"
        )
    except (TimeoutError, OSError) as e:
        raise RuntimeError(f"DeepSeek API request failed: {e}")


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
    task = arguments["task"]
    context = arguments["context"]
    failure_log = arguments["failure_log"]
    output_path = arguments.get(
        "output", ".deepseek-forge/fix.patch.diff"
    )

    # Sanitize sensitive data before sending to API
    failure_log = _sanitize_log_content(failure_log)

    endpoint = os.environ.get(
        "DEEPSEEK_ENDPOINT", "https://api.deepseek.com/chat/completions"
    )
    api_key_env = os.environ.get("DEEPSEEK_API_KEY_ENV", "DEEPSEEK_API_KEY")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"API key environment variable '{api_key_env}' is not set"
        )

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
    temperature = float(os.environ.get("DEEPSEEK_TEMPERATURE", "0.2"))
    timeout = int(os.environ.get("DEEPSEEK_TIMEOUT", "120"))

    system_prompt = _read_template("fix_tests")

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

    raw_response = _call_api(endpoint, api_key, model, messages, temperature, timeout)
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
