<div align="center">

# deepseek-forge

**Codex plans · DeepSeek patches · Codex verifies**

DeepSeek-backed development orchestration for Codex, packaged as a Skill + MCP + Plugin.

<strong>English</strong> · <a href="./README.zh-CN.md">简体中文</a>

![plugin](https://img.shields.io/badge/codex-plugin-24292f)
![version](https://img.shields.io/badge/version-v0.1.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.11-3776ab)
![tests](https://img.shields.io/badge/tests-213%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-6f42c1)

</div>

## Overview

`deepseek-forge` lets Codex delegate patch generation to DeepSeek while keeping Codex as the executor and reviewer.

The intended loop is:

```text
Codex creates a plan
DeepSeek generates a unified diff
Codex validates and reviews the diff
Codex applies the patch
Codex runs tests, lint, and typecheck
If checks fail, Codex sends sanitized logs back to DeepSeek for a fix patch
```

DeepSeek never runs shell commands, never touches the filesystem, and never commits code. It only returns text patches. Codex owns all local actions.

## What You Get

| Component | Purpose |
|---|---|
| `deepseek-forge` Skill | Guides Codex through planning, context collection, patch generation, validation, review, apply, checks, and fix loops. |
| `deepseek-mcp` MCP server | Exposes structured DeepSeek tools for implementation, test fixing, patch review, patch explanation, and planning. |
| Safe patch scripts | Collect context, call DeepSeek, validate patches, apply patches, and run project checks. |
| Codex plugin package | Installable plugin bundle under `deepseek-forge/`. |

## Requirements

- Python 3.11+
- Git
- Bash
- DeepSeek API key
- Codex with local plugin support

No Python package installation is required for the core scripts. They use the Python standard library.

## Install The Plugin

1. Clone or open this repository:

```bash
git clone <repo-url>
cd deepseek-forge
```

2. Configure your DeepSeek API key:

```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"
```

3. Install or import the local plugin directory in Codex:

```text
deepseek-forge/
```

The plugin package contains:

```text
deepseek-forge/
├── .codex-plugin/plugin.json
├── .mcp.json
├── skills/deepseek-forge/
└── mcp/deepseek-mcp/
```

If your Codex CLI supports local plugin installation, the package can be installed from the repository root:

```bash
codex plugin install ./deepseek-forge
```

If your Codex app uses a plugin manager UI, import or add the `deepseek-forge/` folder as a local plugin.

## Use It In Codex

Open a Codex session in the target repository and ask Codex to use the plugin Skill:

```text
Use the deepseek-forge skill to implement this task with DeepSeek:
<describe the feature or bug fix>
```

For example:

```text
Use the deepseek-forge skill to add input validation to the user signup endpoint.
DeepSeek should generate the patch, and Codex should apply it only after review.
```

Codex should then run the workflow:

1. Create `.deepseek-forge/plan.md`.
2. Collect repository context into `.deepseek-forge/repo_context.md`.
3. Ask DeepSeek for a unified diff patch.
4. Validate the patch with `apply_patch_safe.py --check`.
5. Review the patch before applying it.
6. Apply the patch with `apply_patch_safe.py --apply`.
7. Run `run_checks.sh`.
8. If checks fail, send sanitized failure logs to DeepSeek and request a fix patch.

## Available MCP Tools

| Tool | Use |
|---|---|
| `deepseek.plan` | Create a structured implementation plan. |
| `deepseek.implement` | Generate a unified diff patch from task, plan, and context. |
| `deepseek.fix_tests` | Generate a fix patch from sanitized failure logs. |
| `deepseek.review_patch` | Review a patch for correctness, safety, and completeness. |
| `deepseek.explain_patch` | Explain what a patch changes. |

## Manual CLI Workflow

You normally let Codex run these steps through the Skill. For development or debugging, you can run the scripts directly from this repository.

1. Create a task file:

```bash
cat > task.md <<'EOF'
Add a hello_world() function to src/main.py that returns "Hello, World!".
EOF
```

2. Collect repository context:

```bash
mkdir -p .deepseek-forge

python3 scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md
```

3. Generate a patch:

```bash
python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff
```

4. Validate and apply:

```bash
python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --check

python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --apply
```

5. Run checks:

```bash
scripts/run_checks.sh
```

If checks fail, generate a fix patch:

```bash
python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/fix.patch.diff \
  --template fix_tests \
  --failure-log .deepseek-forge/check.log
```

## Configuration

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `DEEPSEEK_API_KEY` | Yes | none | DeepSeek API key. |
| `DEEPSEEK_ENDPOINT` | No | `https://api.deepseek.com/chat/completions` | Override the API endpoint for MCP tools. |
| `DEEPSEEK_MODEL` | No | `deepseek-v4-pro` | Default model for MCP tools. |
| `DEEPSEEK_TEMPERATURE` | No | `0.2` | Sampling temperature. |
| `DEEPSEEK_TIMEOUT` | No | `120` | API timeout in seconds. |
| `DEEPSEEK_TEMPLATE_PATH` | No | auto-detected | Override prompt template location for MCP tools. |
| `CHECK_COMMANDS` | No | auto-detected | Override `run_checks.sh` with explicit check commands. |

Runtime files are written under `.deepseek-forge/`:

| File | Purpose |
|---|---|
| `plan.md` | Codex implementation plan. |
| `repo_context.md` | Repository context sent to DeepSeek. |
| `patch.diff` | Primary DeepSeek patch. |
| `fix.patch.diff` | Patch generated from failing checks. |
| `check.log` | Test, lint, and typecheck output. |

## Safety Rules

- DeepSeek only outputs unified diffs.
- DeepSeek cannot run shell commands.
- DeepSeek cannot commit to git.
- Patches are rejected for absolute paths, path traversal, `.git/` changes, empty targets, and file deletions by default.
- Failure logs are sanitized before being sent back to DeepSeek.
- Codex performs the final review and decides whether to commit.

## Verify The Repository

```bash
python3 -m py_compile scripts/*.py
python3 -m py_compile deepseek-mcp/server.py deepseek-mcp/tools/*.py
bash -n scripts/run_checks.sh
python3 -m unittest discover
python3 <path-to-plugin-creator>/scripts/validate_plugin.py ./deepseek-forge
```

Current local verification:

```text
213 tests, 0 failures, 0 errors
Plugin validation passed
```

## License

MIT
