---
name: deepseek-forge
description: >
  Orchestrates Codex planning with DeepSeek implementation. When a user asks
  Codex to implement features, fix bugs, or generate code patches using DeepSeek,
  this skill coordinates the workflow: Codex plans the work, DeepSeek generates
  unified diff patches, Codex reviews and applies them safely, runs checks,
  and manages the fix-retry loop.
---

# deepseek-forge Skill

Codex plans the work; DeepSeek outputs unified diffs. Codex is the sole executor.

---

## Trigger Scenarios

Activate this skill when the user:

- Asks Codex to implement code using DeepSeek
- Wants to generate a patch with DeepSeek
- Needs to fix test failures with DeepSeek assistance
- Asks for a patch review from DeepSeek
- Says "use deepseek" or "delegate to deepseek"

---

## Standard Workflow

Follow these steps in order. Do not skip safety steps.

1. **Understand the task.** Read the user's request. Create a plan document at `.deepseek-forge/plan.md` that captures the scope, affected files, and expected behavior.

2. **Collect repository context.** Run `scripts/collect_context.py` to gather relevant source files, project configs, and git state into a single Markdown file. This prevents sending irrelevant or oversized files to DeepSeek.

3. **Generate the patch.** Run `scripts/deepseek_worker.py` with the `implement_patch` template. DeepSeek receives the task, plan, and repository context, and must produce a unified diff.

4. **Validate the patch.** Run `scripts/apply_patch_safe.py --check` to run all safety validations and `git apply --check` without modifying the working tree.

5. **Review the patch.** As Codex, inspect the diff contents. Check for correctness, completeness, and safety before any application.

6. **Apply the patch.** Run `scripts/apply_patch_safe.py --apply`. This re-runs all checks and then applies the diff.

7. **Run project checks.** Execute `scripts/run_checks.sh` to auto-detect and run the project's test suite, linter, and type checker.

8. **If checks pass:** Proceed to the Codex Final Review Checklist.

9. **If checks fail:** Enter the Fix Loop.

---

## Fix Loop

When `run_checks.sh` fails, attempt automated repair with DeepSeek:

1. Run `scripts/deepseek_worker.py` with `--template fix_tests --failure-log .deepseek-forge/check.log`.

2. Validate the fix patch with `scripts/apply_patch_safe.py --check`.

3. Apply the fix patch with `scripts/apply_patch_safe.py --apply`.

4. Re-run `scripts/run_checks.sh`.

5. **Maximum 3 automatic fix attempts.** If all 3 fail:
   - Stop all automated repair.
   - Report the last failure reason.
   - Point the user to the last patch at `.deepseek-forge/fix.patch.diff`.

---

## Safety Rules

These constraints are non-negotiable and apply to every interaction:

- **DeepSeek only outputs unified diffs.** It never executes commands. It never accesses the filesystem.
- **DeepSeek never runs git commit or any shell command.** Git operations are Codex's exclusive domain.
- **DeepSeek never deletes files directly.** If file deletion is warranted, DeepSeek must request Codex confirmation. The MVP does not automatically apply deletions.
- **Codex is the sole executor.** Only Codex applies patches, runs commands, modifies the repository, and commits.
- **All patches MUST pass `apply_patch_safe.py --check` before any application.** No exceptions.
- **No git commit happens automatically.** Codex makes the final decision on whether and when to commit.

---

## Codex Final Review Checklist

After a patch is applied and checks pass, conduct the following before considering the task complete:

```bash
# Check for whitespace issues
git diff --check

# See which files changed and the extent of changes
git diff --stat

# Review every changed line
git diff
```

During review, verify:

- [ ] No hidden shell execution or command injection
- [ ] No dangerous file operations (absolute paths, `..` traversal, `.git/` modifications)
- [ ] No credential leaks, API keys, or secrets in the diff
- [ ] No modifications to unrelated files outside the task scope
- [ ] No file deletions (unless explicitly requested and confirmed)
- [ ] No test bypasses, relaxed assertions, or reduced validation
- [ ] No obvious bugs, type errors, or missing edge cases

---

## Reference Documents

Read these files when the situation demands deeper context:

- **`references/workflow.md`**: Detailed workflow steps, examples, and edge case handling. Read when the standard workflow needs clarification or when debugging unexpected behavior.

- **`references/prompt_templates.md`**: The exact prompt templates sent to DeepSeek (`implement_patch`, `fix_tests`, `review_patch`). Read when tweaking template behavior or troubleshooting DeepSeek output quality.

---

## Quick Start Commands

The canonical MVP command sequence. Run all of these from the repository root:

```bash
# Prepare the runtime directory
mkdir -p .deepseek-forge

# Step 1: Collect repository context
python3 scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

# Step 2: Generate patch via DeepSeek
python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

# Step 3: Validate the patch before applying
python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --check

# Step 4: Apply the validated patch
python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --apply

# Step 5: Run the project's test and lint suite
scripts/run_checks.sh
```

---

## Fix Loop Commands

When `run_checks.sh` fails, use this sequence:

```bash
# Generate a fix patch using failure log
python3 scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/fix.patch.diff \
  --template fix_tests \
  --failure-log .deepseek-forge/check.log

# Validate the fix patch
python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/fix.patch.diff \
  --check

# Apply the fix patch
python3 scripts/apply_patch_safe.py \
  --patch .deepseek-forge/fix.patch.diff \
  --apply

# Re-run checks
scripts/run_checks.sh
```
