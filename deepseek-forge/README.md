# deepseek-forge

Codex plugin that uses DeepSeek as a safe patch generator.

Codex plans, validates, applies, and runs checks. DeepSeek only returns unified diffs.

## Quick Start

1. Install this plugin folder:

```bash
codex plugin marketplace add .
codex plugin add deepseek-forge@deepseek-forge
```

If you use the Codex app plugin manager, import this `deepseek-forge/` folder.

2. Set your API key:

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

## Safety

- DeepSeek does not run commands.
- DeepSeek does not edit files directly.
- DeepSeek does not commit code.
- Codex validates and reviews patches before applying them.
- Failure logs are sanitized before they are sent to DeepSeek.

## Optional Manual Debugging

Most users should let Codex run the skill. For debugging:

```bash
mkdir -p .deepseek-forge

python3 skills/deepseek-forge/scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 skills/deepseek-forge/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

python3 skills/deepseek-forge/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --check
python3 skills/deepseek-forge/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --apply
bash skills/deepseek-forge/scripts/run_checks.sh
```

Advanced debugging variables:

| Variable | Purpose |
|---|---|
| `DEEPSEEK_TEMPLATE_PATH` | Override prompt template auto-detection. |
| `CHECK_COMMANDS` | Override `run_checks.sh` with explicit commands. |

## Included Tools

| Tool | Purpose |
|---|---|
| `deepseek.plan` | Create an implementation plan. |
| `deepseek.implement` | Generate a patch. |
| `deepseek.fix_tests` | Generate a fix patch from failure logs. |
| `deepseek.review_patch` | Review a patch. |
| `deepseek.explain_patch` | Explain a patch. |

## Requirements

- Python 3.11+
- Git
- Bash
- DeepSeek API key

## License

MIT
