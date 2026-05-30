"""Tool: deepseek.review_candidate_patch — Review a candidate implementation patch."""

from . import config


REVIEW_CANDIDATE_SCHEMA = {
    "description": "Review a candidate implementation patch against a todo item and acceptance criteria",
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
            "patch": {
                "type": "string",
                "description": "The candidate unified diff patch to review",
            },
            "context": {
                "type": "string",
                "description": "Optional repository context",
            },
        },
        "required": ["todo_id", "todo_title", "todo_description", "acceptance", "patch"],
    },
}


def handle_review_candidate_patch(arguments: dict) -> dict:
    cfg = config.get_config()
    todo_id = arguments["todo_id"]
    todo_title = arguments["todo_title"]
    todo_description = arguments["todo_description"]
    acceptance = arguments["acceptance"]
    patch = arguments["patch"]
    context = arguments.get("context", "")

    system_prompt = config.read_template("review_candidate_patch")

    user_content = (
        f"# Todo Item\n\n"
        f"**ID:** {todo_id}\n**Title:** {todo_title}\n**Description:** {todo_description}\n"
        f"\n# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Candidate Patch\n\n{patch}"
    )
    if context:
        user_content += f"\n\n# Repository Context\n\n{context}"

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
    return config.extract_json(raw_response)
