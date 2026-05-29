<div align="center">

# deepseek-forge

**Let Codex use DeepSeek as a safe patch generator.**

Codex plans and verifies. DeepSeek returns unified diffs. You keep local control.

<strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a>

![plugin](https://img.shields.io/badge/codex-plugin-24292f)
![version](https://img.shields.io/badge/version-v0.1.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.11-3776ab)
![tests](https://img.shields.io/badge/tests-391%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-6f42c1)

</div>

## Quick Start

1. Install the local plugin:

```bash
git clone git@github.com:SivanCola/deepseek-forge.git
cd deepseek-forge
codex plugin marketplace add .
codex plugin add deepseek-forge@deepseek-forge
```

If you use the Codex app plugin manager, import `plugins/deepseek-forge/`.

2. Set your DeepSeek API key:

Write the settings to `~/.zshrc`:

```bash
echo 'export DEEPSEEK_API_KEY="your-deepseek-api-key"' >> ~/.zshrc
echo 'export DEEPSEEK_MODEL="deepseek-v4-pro"' >> ~/.zshrc
echo 'export DEEPSEEK_REASONING_EFFORT="max"' >> ~/.zshrc
source ~/.zshrc
```

Or write the same settings to `~/.profile`:

```bash
echo 'export DEEPSEEK_API_KEY="your-deepseek-api-key"' >> ~/.profile
echo 'export DEEPSEEK_MODEL="deepseek-v4-pro"' >> ~/.profile
echo 'export DEEPSEEK_REASONING_EFFORT="max"' >> ~/.profile
source ~/.profile
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
| `DEEPSEEK_FORGE_HOME` | No | `skills/deepseek-forge` inside the installed plugin | Skill root directory of the deepseek-forge installation (where `SKILL.md` and `references/prompt_templates.md` live). |
| `DEEPSEEK_FORGE_ARTIFACT_DIR` | No | `/tmp/deepseek-forge-{pid}/` | Where runtime artifacts (patches, logs, context files) are written. Set to `.deepseek-forge` to keep artifacts in the target repo. |

With 1M context enabled, context collection defaults to 200 files and 500,000 bytes. With it disabled, defaults are 80 files and 120,000 bytes.

## What Happens

```text
1. Codex writes a plan.
2. Codex classifies the task (patch, patch review, PR branch topology).
3. Codex collects repository context (full source or lightweight metadata, per mode).
4. For patch tasks, DeepSeek returns a unified diff. For topology tasks, local scripts generate a dry-run branch split plan.
5. Codex validates and reviews the output.
6. Codex applies the result safely (patch or branch commands).
7. Codex runs checks.
8. If checks fail, Codex sends sanitized logs to DeepSeek for a fix patch.
```

DeepSeek never runs commands, edits files, applies patches, or commits code. It only returns text diffs.

## Patch Review

Use the `deepseek.review_patch` MCP tool (or the `patch_review_task` classification) to have DeepSeek review an existing patch. The workflow is identical to the standard patch generation but uses the `review_patch` prompt template. DeepSeek returns a structured review with correctness concerns, style notes, and safety flags.

## PR Branch Topology Mode

When multiple PRs share the same head SHA (e.g., stacked or chained branches that need independent review), deepseek-forge can detect the topology and generate a safe branch split plan.

### When to Use

- Multiple PRs point at the same commit SHA on the same base ref
- You need to split a monolithic branch into independent, reviewable PR branches
- Branch surgery is required to isolate per-PR file changes

### Workflow

1. **Task classification.** The `task_classifier.py` module detects a `pr_branch_topology_task` and routes to branch surgery mode.
2. **Lightweight context.** `collect_context.py --mode pr-branch-topology` gathers only git/PR metadata (no source code), keeping the payload small.
3. **Split plan generation.** `branch_surgery.py` analyzes shared heads, computes per-PR commit ranges and file lists, and produces safe push commands.
4. **Manual review.** The generated plan is dry-run only. You review each split command before execution.
5. **Post-push verification.** A checklist confirms commits, files, head SHA, and base ref for each pushed branch.

### Safety Guarantees

- **Dry-run only.** `branch_surgery.py` never auto-executes git mutations. No commits, no pushes.
- **Force-with-lease.** Every push command uses `--force-with-lease=<remote-ref>:<expected-sha>`, ensuring the remote ref matches expectations before overwriting.
- **Manual-review fallback.** If PRs share both base and head, commit isolation is not possible; cherry-pick and push commands are commented out until a human verifies the split.
- **Shell-safe command rendering.** Generated shell commands validate PR refs, commit SHAs, PR numbers, and remotes before rendering them, using a conservative ref whitelist for branch names.
- **Fork-aware push and rollback.** Fork PRs use the resolved fork remote or SSH URL for fetch, push, and rollback instructions.
- **Human-in-the-loop.** All commands require manual execution after review.

### Manual Usage

```bash
# Detect shared heads and generate a split plan (auto-fetches PRs via gh CLI)
python3 ${DEEPSEEK_FORGE_HOME}/scripts/branch_surgery.py \
  --output .deepseek-forge/branch_surgery.md

# Or pre-collect PR data and pass it explicitly
python3 ${DEEPSEEK_FORGE_HOME}/scripts/collect_context.py \
  --mode pr-branch-topology \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 ${DEEPSEEK_FORGE_HOME}/scripts/branch_surgery.py \
  --output .deepseek-forge/branch_surgery.md \
  --pr-list "$(gh pr list --json number,title,headRefName,baseRefName,headRefOid,baseRefOid,state,url,headRepository,headRepositoryOwner --state open --limit 50)"
```

## Files Created

Runtime files are written under `DEEPSEEK_FORGE_ARTIFACT_DIR` — which defaults to `/tmp/deepseek-forge-{pid}/`. Set `DEEPSEEK_FORGE_ARTIFACT_DIR=.deepseek-forge` to keep artifacts in the target repository, or set it to any custom path.

If using `.deepseek-forge/` as the artifact directory, consider adding it to `.git/info/exclude` to keep it out of version control:

```bash
echo '.deepseek-forge/' >> .git/info/exclude
```

| File | Purpose |
|---|---|
| `plan.md` | Codex implementation plan. |
| `repo_context.md` | Context sent to DeepSeek. |
| `patch.diff` | Primary patch. |
| `fix.patch.diff` | Patch generated after failed checks. |
| `check.log` | Test, lint, and typecheck output. |

## Optional Manual Debugging

Most users should let Codex run the skill. For plugin development or debugging, the same steps can be run directly.

When deepseek-forge is installed as a plugin, use ``${DEEPSEEK_FORGE_HOME}`` to locate the scripts:

```bash
mkdir -p .deepseek-forge

python3 ${DEEPSEEK_FORGE_HOME}/scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 ${DEEPSEEK_FORGE_HOME}/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --check
python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --apply
bash ${DEEPSEEK_FORGE_HOME}/scripts/run_checks.sh
```

For repo-internal debugging (e.g., when hacking on deepseek-forge itself), use the marketplace plugin path:

```bash
mkdir -p .deepseek-forge

python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --check
python3 plugins/deepseek-forge/skills/deepseek-forge/scripts/apply_patch_safe.py --patch .deepseek-forge/patch.diff --apply
bash plugins/deepseek-forge/skills/deepseek-forge/scripts/run_checks.sh
```

Local plugin installs are copied into Codex's plugin cache. After changing this
repository, refresh the installed plugin with:

```bash
codex plugin remove deepseek-forge@deepseek-forge
codex plugin add deepseek-forge@deepseek-forge
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

Current local result: `391 tests, 0 failures`.

## License

This project is licensed under the MIT License. See [LICENSE](./LICENSE) for
the full license text.
