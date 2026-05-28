# deepseek-mcp

MCP server used by `deepseek-forge`.

It exposes DeepSeek-backed tools for plan generation, patch generation, fix patches, patch review, and patch explanation.

## Configuration

Runtime configuration comes from environment variables, not from `config.example.toml`.

| Variable | Required | Default |
|---|---:|---|
| `DEEPSEEK_API_KEY` | Yes | none |
| `DEEPSEEK_MODEL` | No | `deepseek-v4-pro` |
| `DEEPSEEK_REASONING_EFFORT` | No | `max` |
| `DEEPSEEK_ENABLE_1M_CONTEXT` | No | `true` |

## Tools

| Tool | Purpose |
|---|---|
| `deepseek.plan` | Create an implementation plan. |
| `deepseek.implement` | Generate a unified diff patch. |
| `deepseek.fix_tests` | Generate a fix patch from failure logs. |
| `deepseek.review_patch` | Review a patch. |
| `deepseek.explain_patch` | Explain a patch. |

See the plugin [README.md](../../README.md) for normal usage.
