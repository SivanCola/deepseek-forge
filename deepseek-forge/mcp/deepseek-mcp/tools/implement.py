"""Tool: deepseek.implement — Generate implementation patch via DeepSeek API."""

import os
import sys

from . import config

IMPLEMENT_SCHEMA = {
    "description": "Generate a unified diff patch implementing requested changes using DeepSeek",
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task description",
            },
            "context": {
                "type": "string",
                "description": "Repository context (from collect_context.py or manual)",
            },
            "plan": {
                "type": "string",
                "description": "Implementation plan content",
            },
            "output": {
                "type": "string",
                "description": "Path to write the patch file",
                "default": ".deepseek-forge/patch.diff",
            },
        },
        "required": ["task", "context"],
    },
}


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


def handle_implement(arguments: dict) -> dict:
    cfg = config.get_config()
    task = arguments["task"]
    context = arguments["context"]
    plan = arguments.get("plan", "")
    output_path = arguments.get("output", ".deepseek-forge/patch.diff")

    system_prompt = config.read_template("implement_patch")

    user_content = f"# Task\n\n{task}\n\n# Repository Context\n\n{context}"
    if plan:
        user_content += f"\n\n# Plan\n\n{plan}"

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
