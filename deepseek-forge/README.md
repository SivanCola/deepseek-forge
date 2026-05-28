# deepseek-forge

Codex Plugin that orchestrates Codex planning with DeepSeek code generation. Codex plans the work, DeepSeek generates unified diff patches, Codex reviews and applies them safely.

## What This Plugin Does

This plugin provides a structured collaboration between Codex and DeepSeek:

- **Skill: `deepseek-forge`** -- Orchestrates the full workflow: plan, collect context, generate patch via DeepSeek, validate, review, apply, and verify. Handles the fix-retry loop when checks fail.
- **MCP Server: `deepseek-mcp`** -- Exposes structured tools (`deepseek.implement`, `deepseek.fix_tests`, `deepseek.review_patch`, `deepseek.explain_patch`, `deepseek.plan`) for programmatic DeepSeek integration.

DeepSeek operates as a **diff-only interface**: it never touches the filesystem, never executes commands, and never interacts with git. Codex is the sole executor.

## Installation

1. Copy the `deepseek-forge/` directory into your Codex plugins directory.
2. Install the Codex Plugin via the Codex plugin manager:

```
codex plugin install ./deepseek-forge
```

Or manually add the plugin to your Codex configuration.

## Configuration

Set the `DEEPSEEK_API_KEY` environment variable:

```bash
export DEEPSEEK_API_KEY="your-api-key-here"
```

## Quick Start

```bash
# Prepare the runtime directory
mkdir -p .deepseek-forge

# Collect repository context
python3 skills/deepseek-forge/scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

# Generate patch via DeepSeek
python3 skills/deepseek-forge/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

# Validate the patch
python3 skills/deepseek-forge/scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --check

# Apply the patch
python3 skills/deepseek-forge/scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --apply

# Run project checks
bash skills/deepseek-forge/scripts/run_checks.sh
```

## Available Skills

| Skill | Description |
|-------|-------------|
| `deepseek-forge` | Orchestrates the full Codex-DeepSeek workflow: planning, context collection, patch generation, validation, review, application, verification, and fix-retry loop |

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `deepseek.implement` | Generate a unified diff patch implementing requested changes |
| `deepseek.fix_tests` | Generate a fix patch based on failure logs |
| `deepseek.review_patch` | Review a patch for correctness, safety, and completeness |
| `deepseek.explain_patch` | Explain what a patch does in plain language |
| `deepseek.plan` | Generate an implementation plan from a natural language task |

## Safety Model

- **DeepSeek only outputs unified diffs** -- it never executes commands or accesses the filesystem
- **All patches pass safety validation** before any application (no shell injection, no path traversal, no file deletions without confirmation)
- **Codex is the sole executor** -- only Codex applies patches, runs commands, and modifies the repository
- **Fix-retry loop** with a maximum of 3 automatic attempts

## Workflow Steps

1. **Plan** -- Codex creates a plan document
2. **Collect Context** -- Gather relevant source files and git state
3. **Generate Patch** -- DeepSeek produces a unified diff
4. **Validate** -- Run safety checks and `git apply --check`
5. **Review** -- Codex inspects the diff for correctness
6. **Apply** -- Apply the validated patch
7. **Verify** -- Run project tests, linter, and type checker

## Requirements

- Python 3.11 or later
- Git (for `git apply`, `git diff`, `git status`)
- Bash (for `run_checks.sh`)
- DeepSeek API key (`DEEPSEEK_API_KEY` environment variable)

## License

MIT
