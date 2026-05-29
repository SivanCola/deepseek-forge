"""Tools: deepseek.review_patch and deepseek.explain_patch via DeepSeek API."""

import json

from . import config

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
    cfg = config.get_config()
    task = arguments["task"]
    patch = arguments["patch"]

    system_prompt = config.read_template("review_patch")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"# Task\n\n{task}\n\n# Patch to Review\n\n{patch}"},
    ]

    request_body = config.build_request_body(
        cfg["model"], messages, cfg["reasoning_effort"]
    )
    raw_response = config.call_api(
        cfg["endpoint"], cfg["api_key"], request_body, cfg["timeout"]
    )
    return _parse_json_response(raw_response)


def handle_explain_patch(arguments: dict) -> dict:
    cfg = config.get_config()
    patch = arguments["patch"]

    messages = [
        {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
        {"role": "user", "content": f"# Patch\n\n{patch}"},
    ]

    request_body = config.build_request_body(
        cfg["model"], messages, cfg["reasoning_effort"]
    )
    raw_response = config.call_api(
        cfg["endpoint"], cfg["api_key"], request_body, cfg["timeout"]
    )
    return _parse_json_response(raw_response)
