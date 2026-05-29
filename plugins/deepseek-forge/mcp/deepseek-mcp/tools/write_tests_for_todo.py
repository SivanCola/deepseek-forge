"""Tool: deepseek.write_tests_for_todo — Generate tests for a todo item."""

import os
import sys

from . import config


WRITE_TESTS_SCHEMA = {
    "description": "Generate a unified diff patch with tests for a specific todo item using DeepSeek",
    "inputSchema": {
        "type": "object",
        "properties": {
            "todo_id": {
                "type": "string",
                "description": "The todo item id",
            },
            "todo_title": {
                "type": "string",
                "description": "The todo item title",
            },
            "todo_description": {
                "type": "string",
                "description": "The todo item description",
            },
            "acceptance": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Acceptance criteria",
            },
            "implementation_patch": {
                "type": "string",
                "description": "The implementation patch that was applied",
            },
            "context": {
                "type": "string",
                "description": "Repository context",
            },
            "output": {
                "type": "string",
                "description": "Path to write the test patch",
                "default": ".deepseek-forge/tests_todo.diff",
            },
        },
        "required": ["todo_id", "todo_title", "todo_description", "acceptance", "implementation_patch", "context"],
    },
}


def _extract_diff(response_text: str) -> str:
    text = response_text.strip()
    if text.startswith("```diff"):
        print("[deepseek-forge-mcp] Warning: removing diff code fences from response", file=sys.stderr)
        text = text[len("```diff"):].strip()
    elif text.startswith("```"):
        print("[deepseek-forge-mcp] Warning: removing code fences from response", file=sys.stderr)
        text = text[len("```"):].strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    lines = text.split("\n")
    diff_start = None
    diff_end = None
    diff_prefixes = ("+", "-", " ", "@@", "---", "+++", "\\")

    for i, line in enumerate(lines):
        if diff_start is None and (line.startswith("--- a/") or line.startswith("--- /dev/null")):
            diff_start = i
        if line and any(line.startswith(p) for p in diff_prefixes):
            diff_end = i

    if diff_start is None or diff_end is None or diff_end < diff_start:
        raise ValueError("Response contains no valid unified diff")

    diff_text = "\n".join(lines[diff_start:diff_end + 1])
    if not diff_text or "--- " not in diff_text:
        raise ValueError("Response contains no valid unified diff")
    return diff_text


def handle_write_tests_for_todo(arguments: dict) -> dict:
    cfg = config.get_config()
    todo_id = arguments["todo_id"]
    todo_title = arguments["todo_title"]
    todo_description = arguments["todo_description"]
    acceptance = arguments["acceptance"]
    implementation_patch = arguments["implementation_patch"]
    context = arguments["context"]
    output_path = arguments.get("output", ".deepseek-forge/tests_todo.diff")

    system_prompt = config.read_template("write_tests_for_todo")

    user_content = (
        f"# Todo Item\n\n"
        f"**ID:** {todo_id}\n**Title:** {todo_title}\n**Description:** {todo_description}\n"
        f"\n# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Implementation Patch\n\n{implementation_patch}"
        f"\n\n# Repository Context\n\n{context}"
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
    }
