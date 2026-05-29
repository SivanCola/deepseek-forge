"""Tool: deepseek.fix_open_bugs — Generate a fix patch for open bugs."""

import os
import sys

from . import config


FIX_OPEN_BUGS_SCHEMA = {
    "description": "Generate a unified diff patch that fixes open bugs using DeepSeek",
    "inputSchema": {
        "type": "object",
        "properties": {
            "bugs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "severity": {"type": "string"},
                        "failure_signature": {"type": "string"},
                    },
                },
                "description": "List of open bug objects",
            },
            "acceptance": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Acceptance criteria",
            },
            "context": {
                "type": "string",
                "description": "Repository context",
            },
            "patch_history": {
                "type": "string",
                "description": "Summary of patches applied so far",
            },
            "output": {
                "type": "string",
                "description": "Path to write the fix patch",
                "default": ".deepseek-forge/fix_bugs.diff",
            },
        },
        "required": ["bugs", "acceptance", "context"],
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


def handle_fix_open_bugs(arguments: dict) -> dict:
    cfg = config.get_config()
    bugs = arguments["bugs"]
    acceptance = arguments["acceptance"]
    context = arguments["context"]
    patch_history = arguments.get("patch_history", "")
    output_path = arguments.get("output", ".deepseek-forge/fix_bugs.diff")

    system_prompt = config.read_template("fix_open_bugs")

    bugs_text = "\n".join(
        f"- **{b.get('id', '?')}** [{b.get('severity', 'error')}] {b.get('title', '')}\n  {b.get('description', '')}"
        for b in bugs
    )

    user_content = (
        f"# Open Bugs\n\n{bugs_text}\n\n"
        f"# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Repository Context\n\n{context}"
    )
    if patch_history:
        user_content += f"\n\n# Patch History\n\n{patch_history}"

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
