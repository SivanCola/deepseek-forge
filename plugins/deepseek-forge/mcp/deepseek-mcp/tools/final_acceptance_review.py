"""Tool: deepseek.final_acceptance_review — Final acceptance assessment of complete implementation."""

from . import config


FINAL_ACCEPTANCE_REVIEW_SCHEMA = {
    "description": "Review the complete implementation diff against acceptance criteria and check results",
    "inputSchema": {
        "type": "object",
        "properties": {
            "acceptance": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Acceptance criteria",
            },
            "diff": {
                "type": "string",
                "description": "The full implementation diff",
            },
            "check_log": {
                "type": "string",
                "description": "Combined check output (tests, lint, typecheck)",
            },
        },
        "required": ["acceptance", "diff", "check_log"],
    },
}


def handle_final_acceptance_review(arguments: dict) -> dict:
    cfg = config.get_config()
    acceptance = arguments["acceptance"]
    diff = arguments["diff"]
    check_log = arguments["check_log"]

    system_prompt = config.read_template("final_acceptance_review")

    user_content = (
        f"# Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in acceptance) +
        f"\n\n# Full Implementation Diff\n\n{diff}\n\n"
        f"# Check Results\n\n{check_log}"
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
    return config.extract_json(raw_response)
