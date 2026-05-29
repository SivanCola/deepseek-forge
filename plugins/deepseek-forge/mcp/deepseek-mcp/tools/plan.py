"""Tool: deepseek.plan — Generate a structured implementation plan."""

import os

from . import config

PLAN_SCHEMA = {
    "description": "Generate a structured implementation plan for a coding task",
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task description",
            },
            "output": {
                "type": "string",
                "description": "Path to write the plan file. Defaults to the deepseek-forge artifact directory.",
            },
        },
        "required": ["task"],
    },
}

PLAN_TEMPLATE = """# Implementation Plan

## Task Overview
{task}

## Files to Change
<!-- Identify which files need to be created, modified, or deleted -->

## Implementation Steps
<!-- Numbered steps for the implementation -->

1.

## Testing Strategy
<!-- How to verify the changes work -->

## Risks and Edge Cases
<!-- Potential issues to watch for -->
"""


def handle_plan(arguments: dict) -> dict:
    task = arguments["task"]
    output_path = arguments.get("output") or config.get_artifact_path("plan.md")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    plan_content = PLAN_TEMPLATE.format(task=task)

    with open(output_path, "w") as f:
        f.write(plan_content)

    return {
        "plan_path": output_path,
        "plan_size": len(plan_content),
        "sections": [
            "Task Overview",
            "Files to Change",
            "Implementation Steps",
            "Testing Strategy",
            "Risks and Edge Cases",
        ],
    }
