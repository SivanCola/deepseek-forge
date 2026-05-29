<div align="center">

# deepseek-forge

**Let Codex use DeepSeek as a safe patch generator.**

Codex plans and verifies. DeepSeek returns unified diffs. You keep local control.

<strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a>

![plugin](https://img.shields.io/badge/codex-plugin-24292f)
![version](https://img.shields.io/badge/version-v0.1.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.11-3776ab)
![tests](https://img.shields.io/badge/tests-240%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-6f42c1)

</div>

## Quick Start

1. Install the local plugin:

```bash
git clone git@github.com:SivanCola/deepseek-forge.git
cd deepseek-forge
codex plugin install ./deepseek-forge
```

If you use the Codex app plugin manager, import the local `deepseek-forge/` folder instead.

2. Set your DeepSeek API key:

```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"
```

3. Open Codex in your target repository and ask:

```text
Use the deepseek-forge skill to implement:
<describe the feature or bug fix>
```

Codex will collect context, ask DeepSeek for a patch, validate it, review it, apply it, run checks, and request a fix patch if checks fail.

## Trigger Phrases

For reliable activation, mention `deepseek-forge` or `DeepSeek` explicitly:

```text
Use the deepseek-forge skill to implement:
<task>
```

Other useful phrases:

- `use deepseek`
- `delegate this to DeepSeek`
- `ask DeepSeek to generate the patch`
- `use DeepSeek to fix these failing tests`
- `ask DeepSeek to review this patch`
- `DeepSeek should generate the patch; Codex should review, apply, and test it`

## Configuration

Only `DEEPSEEK_API_KEY` is required.

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `DEEPSEEK_API_KEY` | Yes | none | DeepSeek API key. |
| `DEEPSEEK_MODEL` | No | `deepseek-v4-pro` | Model used for patch generation. |
| `DEEPSEEK_REASONING_EFFORT` | No | `max` | `high` or `max`. Compatibility values: `low` / `medium` -> `high`, `xhigh` -> `max`. |
| `DEEPSEEK_ENABLE_1M_CONTEXT` | No | `true` | Enables larger context collection. Set to `false` to reduce cost and latency. |

With 1M context enabled, context collection defaults to 200 files and 500,000 bytes. With it disabled, defaults are 80 files and 120,000 bytes.

## What Happens

```text
1. Codex writes a plan.
2. Codex collects repository context.
3. DeepSeek returns a unified diff.
4. Codex validates and reviews the patch.
5. Codex applies the patch.
6. Codex runs checks.
7. If checks fail, Codex sends sanitized logs to DeepSeek for a fix patch.
```

DeepSeek never runs commands, edits files, applies patches, or commits code. It only returns text diffs.

## Files Created

Runtime files are written under `.deepseek-forge/` in the target repository:

| File | Purpose |
|---|---|
| `plan.md` | Codex implementation plan. |
| `repo_context.md` | Context sent to DeepSeek. |
| `patch.diff` | Primary patch. |
| `fix.patch.diff` | Patch generated after failed checks. |
| `check.log` | Test, lint, and typecheck output. |

## Optional Manual Debugging

Most users should let Codex run the skill. For plugin development or debugging, the same steps can be run directly:

```bash
mkdir -p .deepseek-forge

python3 scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

python3 scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --check
python3 scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --apply
bash scripts/run_checks.sh
```

Advanced debugging environment variables:

| Variable | Purpose |
|---|---|
| `DEEPSEEK_TEMPLATE_PATH` | Override prompt template auto-detection. |
| `CHECK_COMMANDS` | Override `run_checks.sh` with explicit commands. |

## MCP Tools

The plugin includes a `deepseek-forge-mcp` server with these tools:

| Tool | Purpose |
|---|---|
| `deepseek.plan` | Create an implementation plan. |
| `deepseek.implement` | Generate a patch. |
| `deepseek.fix_tests` | Generate a fix patch from failure logs. |
| `deepseek.review_patch` | Review a patch. |
| `deepseek.explain_patch` | Explain a patch. |

## Verify This Repository

```bash
python3 -m unittest discover -s tests -v
```

Current local result: `240 tests, 0 failures`.

## License

MIT
