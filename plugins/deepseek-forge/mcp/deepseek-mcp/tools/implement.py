"""Tool: deepseek.implement — Generate implementation patch via DeepSeek API."""

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
                "description": "Path to write the patch file. Defaults to the deepseek-forge artifact directory.",
            },
        },
        "required": ["task", "context"],
    },
}


def handle_implement(arguments: dict) -> dict:
    cfg = config.get_config()
    task = arguments["task"]
    context = arguments["context"]
    plan = arguments.get("plan", "")
    output_path = arguments.get("output") or config.get_artifact_path("patch.diff")

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
        "warnings": [],
    }
