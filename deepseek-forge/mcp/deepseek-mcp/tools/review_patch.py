"""Tools: deepseek.review_patch and deepseek.explain_patch via DeepSeek API."""

import json
import os
import urllib.request

REVIEW_SCHEMA = {
    "description": "Review a patch for correctness, safety, and completeness",
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The original task description",
            },
            "patch": {
                "type": "string",
                "description": "The unified diff patch content to review",
            },
        },
        "required": ["task", "patch"],
    },
}

EXPLAIN_SCHEMA = {
    "description": "Explain what a patch does in plain language",
    "inputSchema": {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": "The unified diff patch to explain",
            },
        },
        "required": ["patch"],
    },
}

EXPLAIN_SYSTEM_PROMPT = """You are a code explanation assistant. Your function is to read a unified diff patch and explain what it does in plain language.

Output a JSON object with this structure:
{
  "summary": "One-line summary of what the patch does",
  "changed_files": ["file1.py", "file2.py"],
  "description": "Detailed explanation of the changes"
}

CRITICAL RULES:
- Output ONLY the JSON object. No markdown fences. No commentary.
- Do NOT wrap in ```json code fences."""


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


def _get_api_params():
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
    return endpoint, api_key, model, temperature, timeout


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```"):
        text = text[len("```"):].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return json.loads(text)


def handle_review_patch(arguments: dict) -> dict:
    task = arguments["task"]
    patch = arguments["patch"]

    endpoint, api_key, model, temperature, timeout = _get_api_params()
    system_prompt = _read_template("review_patch")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"# Task\n\n{task}\n\n# Patch to Review\n\n{patch}"},
    ]

    raw_response = _call_api(endpoint, api_key, model, messages, temperature, timeout)
    return _parse_json_response(raw_response)


def handle_explain_patch(arguments: dict) -> dict:
    patch = arguments["patch"]

    endpoint, api_key, model, temperature, timeout = _get_api_params()

    messages = [
        {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
        {"role": "user", "content": f"# Patch\n\n{patch}"},
    ]

    raw_response = _call_api(endpoint, api_key, model, messages, temperature, timeout)
    return _parse_json_response(raw_response)
