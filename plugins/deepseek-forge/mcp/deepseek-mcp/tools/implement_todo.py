"""Tool: deepseek.implement_todo — Generate patch for a specific todo item."""

from . import config


IMPLEMENT_TODO_SCHEMA = {
    "description": "Generate a unified diff patch implementing a specific todo item using DeepSeek",
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
            "todo_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to modify for this todo",
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
            "state_json": {
                "type": "string",
                "description": "Current state JSON (what has been completed so far)",
            },
            "output": {
                "type": "string",
                "description": "Path to write the patch file",
                "default": ".deepseek-forge/patch_todo.diff",
            },
        },
        "required": ["todo_id", "todo_title", "todo_description", "acceptance", "context"],
    },
}


def handle_implement_todo(arguments: dict) -> dict:
    cfg = config.get_config()
    todo_id = arguments["todo_id"]
    todo_title = arguments["todo_title"]
    todo_description = arguments["todo_description"]
    todo_files = arguments.get("todo_files", [])
    acceptance = arguments["acceptance"]
    context = arguments["context"]
    state_json = arguments.get("state_json", "")
    output_path = arguments.get("output", ".deepseek-forge/patch_todo.diff")

    system_prompt = config.read_template("implement_todo")

    user_content = (
        f"# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Todo Item\n\n"
        f"**ID:** {todo_id}\n"
        f"**Title:** {todo_title}\n"
        f"**Description:** {todo_description}\n"
        f"**Files:** {', '.join(todo_files)}\n"
        f"\n# Repository Context\n\n{context}"
    )
    if state_json:
        user_content += f"\n\n# Current State\n\n{state_json}"

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
