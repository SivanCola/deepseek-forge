#!/usr/bin/env python3
"""DeepSeek MCP Server — exposes deepseek.* tools via JSON-RPC over stdio."""

import json
import os
import sys
import traceback

from tools.plan import handle_plan, PLAN_SCHEMA
from tools.implement import handle_implement, IMPLEMENT_SCHEMA
from tools.fix_tests import handle_fix_tests, FIX_TESTS_SCHEMA
from tools.review_patch import (
    handle_review_patch,
    REVIEW_SCHEMA,
    handle_explain_patch,
    EXPLAIN_SCHEMA,
)

TOOLS = {
    "deepseek.plan": {"handler": handle_plan, "schema": PLAN_SCHEMA},
    "deepseek.implement": {"handler": handle_implement, "schema": IMPLEMENT_SCHEMA},
    "deepseek.fix_tests": {"handler": handle_fix_tests, "schema": FIX_TESTS_SCHEMA},
    "deepseek.review_patch": {
        "handler": handle_review_patch,
        "schema": REVIEW_SCHEMA,
    },
    "deepseek.explain_patch": {
        "handler": handle_explain_patch,
        "schema": EXPLAIN_SCHEMA,
    },
}

SERVER_INFO = {
    "name": "deepseek-forge-mcp",
    "version": "0.1.0",
    "protocolVersion": "2024-11-05",
}


def _send_response(req_id, result):
    resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _send_error(req_id, code, message):
    resp = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def handle_request(request: dict):
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return _send_response(req_id, {
            "protocolVersion": SERVER_INFO["protocolVersion"],
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}},
        })

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        tools_list = [
            {"name": name, **tool["schema"]} for name, tool in TOOLS.items()
        ]
        return _send_response(req_id, {"tools": tools_list})

    if method == "tools/call":
        tool_name = request["params"]["name"]
        tool_args = request["params"].get("arguments", {})

        if tool_name not in TOOLS:
            return _send_error(req_id, -32601, f"Tool not found: {tool_name}")

        try:
            handler = TOOLS[tool_name]["handler"]
            result = handler(tool_args)
            return _send_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })
        except Exception as e:
            return _send_error(
                req_id, -32000, f"Tool execution error: {str(e)}"
            )

    if method == "ping":
        return _send_response(req_id, {})

    return _send_error(req_id, -32601, f"Unknown method: {method}")


def main():
    print(
        f"[deepseek-forge-mcp] Server starting (v{SERVER_INFO['version']})",
        file=sys.stderr,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            handle_request(request)
        except json.JSONDecodeError as e:
            print(f"[deepseek-forge-mcp] Invalid JSON: {e}", file=sys.stderr)
            _send_error(None, -32700, "Parse error")
        except Exception as e:
            print(f"[deepseek-forge-mcp] Unexpected error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    print("[deepseek-forge-mcp] Server shutting down", file=sys.stderr)


if __name__ == "__main__":
    main()
