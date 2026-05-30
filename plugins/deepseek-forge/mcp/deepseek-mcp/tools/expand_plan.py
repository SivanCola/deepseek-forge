"""Tool: deepseek.expand_plan — Expand task into acceptance criteria, plan, and todos."""

from . import config


EXPAND_PLAN_SCHEMA = {
    "description": "Expand a task description into acceptance criteria, implementation plan, and todo items using DeepSeek",
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task description",
            },
            "context": {
                "type": "string",
                "description": "Optional repository context",
            },
        },
        "required": ["task"],
    },
}


def handle_expand_plan(arguments: dict) -> dict:
    cfg = config.get_config()
    task = arguments["task"]
    context = arguments.get("context", "")

    system_prompt = config.read_template("expand_plan")

    user_content = f"# Task\n\n{task}"
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
