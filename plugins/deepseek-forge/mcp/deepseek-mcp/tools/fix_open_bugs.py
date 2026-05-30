"""Tool: deepseek.fix_open_bugs — Generate a fix patch for open bugs."""

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
    diff_text = config.extract_diff(raw_response)

    output_path = config.validate_output_path(output_path)
    config.ensure_output_dir(output_path)
    with open(output_path, "w") as f:
        f.write(diff_text)

    lines = diff_text.count("\n") + 1
    return {
        "patch_path": output_path,
        "patch_size": len(diff_text),
        "lines": lines,
    }
