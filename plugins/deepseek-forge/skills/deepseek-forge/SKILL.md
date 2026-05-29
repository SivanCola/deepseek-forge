---
name: deepseek-forge
license: MIT
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

## Mandatory Output Fields

Every output from DeepSeek (patch, fix plan, or branch surgery report) MUST include
these four dimensions. Codex must verify all four are present before proceeding.

| # | Dimension | Patch Mode | Branch Surgery Mode |
|---|---|---|---|
| 1 | **涉及 PR / Head Ref** | File paths in the diff (`--- a/`, `+++ b/`) | PR number, head ref, base ref, head SHA |
| 2 | **验证命令** | `run_checks.sh` output | `git log`, `git diff --stat`, `gh pr view --json` commands |
| 3 | **风险点** | No dangerous operations (shell, git, file deletion) | Force-push risk, shared-head risk, cherry-pick conflict risk, fork remote risk |
| 4 | **回滚方案** | `git apply -R` or `git checkout -- <file>` | Backup branch creation + `git push --force-with-lease` restore |

### Codex Verification Checklist

After receiving any DeepSeek output, Codex MUST verify:

- [ ] All affected PRs / files are explicitly listed
- [ ] Verification commands are present and copy-paste runnable
- [ ] Risk points are explicitly stated (not implied)
- [ ] Rollback plan exists and is executable

If any dimension is missing, Codex must request a re-generation or supplement the output
manually before proceeding.

---

## Trigger Scenarios

Activate this skill when the user:

- Asks Codex to implement code using DeepSeek
- Wants to generate a patch with DeepSeek
- Needs to fix test failures with DeepSeek assistance
- Asks for a patch review from DeepSeek
- Needs to split or restructure PR branches (shared head, stacked branches)
- Wants to generate a branch split plan for multiple PRs
- Says "use deepseek" or "delegate to deepseek"

---

## Branch Surgery Workflow

This mode handles PR branch topology tasks — when multiple PRs share the same head SHA and need to be split into independent branches.

### When to Enter This Mode

- The task is classified as `pr_branch_topology_task` by `scripts/task_classifier.py`
- Multiple PRs point at the same commit SHA on a common base ref
- The user asks to split, restructure, or untangle stacked/chained PR branches

### Step-by-Step

1. **Classify the task.** Run task classification to detect the topology scenario. `pr_branch_topology_task` routes to this workflow instead of the standard patch workflow.

2. **Collect lightweight context.** Use `collect_context.py --mode pr-branch-topology` to gather only git/PR metadata — no source code. This keeps the context payload small and focused.

3. **Detect shared heads and generate split plan.** `scripts/branch_surgery.py` analyzes the branch topology, computes per-PR commit ranges and file lists, and produces safe push commands with `--force-with-lease`.

4. **Review the plan.** Inspect each split command. Verify the expected SHA, target branch name, and commit range are correct. **Never execute commands automatically.**

5. **Execute manually.** Run each push command after confirming it matches expectations. Each command uses `--force-with-lease=<remote-ref>:<expected-sha>` for safety.

6. **Post-push verification.** Run the verification checklist:
   - Check commits on each pushed branch match the plan
   - Verify files changed match the plan
   - Confirm head SHA and base ref are correct

**Mandatory output validation:** Before proceeding past the plan stage, verify the
report contains all four mandatory output fields. See [Mandatory Output Fields](#mandatory-output-fields).

### Safety Rules for Branch Surgery

- **Dry-run only.** `branch_surgery.py` never auto-executes git mutations. No commits, no pushes.
- **Force-with-lease is mandatory.** Every push command uses `--force-with-lease=<remote-ref>:<expected-sha>`. Plain `--force` is never emitted.
- **Human-in-the-loop.** All generated commands require manual review and execution.
- **No automatic git writes of any kind.** Branches, tags, and reflogs are never modified by the script.

### Branch Surgery Commands

```bash
# Classify the task (returns task type and routing info)
python3 ${DEEPSEEK_FORGE_HOME}/scripts/task_classifier.py \
  "$(cat task.md)"

# Collect lightweight git/PR context (no source code)
python3 ${DEEPSEEK_FORGE_HOME}/scripts/collect_context.py \
  --mode pr-branch-topology \
  --task task.md \
  --output .deepseek-forge/repo_context.md

# Generate the branch split plan
python3 ${DEEPSEEK_FORGE_HOME}/scripts/branch_surgery.py \
  --output .deepseek-forge/branch_surgery.md
```

### pr_branch_topology_task Handling Notes

When a task is classified as `pr_branch_topology_task`:

- Does **NOT** call `implement_patch`. The standard deepseek_worker patch generation step is skipped.
- Does **NOT** require DeepSeek to produce a unified diff. The output is a branch split plan, not a code patch.
- For full GitHub review tasks (PR comments, inline reviews, approval workflows), prefer using `gh-pr-review-resolver` instead. `deepseek-forge` branch surgery focuses on git topology restructuring, not code-level PR review.

---

## Standard Workflow

Follow these steps in order. Do not skip safety steps.

1. **Understand the task.** Read the user's request. Create a plan document at `.deepseek-forge/plan.md` that captures the scope, affected files, and expected behavior.

2. **Collect repository context.** Run `scripts/collect_context.py` to gather relevant source files, project configs, and git state into a single Markdown file. This prevents sending irrelevant or oversized files to DeepSeek.

3. **Generate the patch.** Run `scripts/deepseek_worker.py` with the `implement_patch` template. DeepSeek receives the task, plan, and repository context, and must produce a unified diff.

**3a. Validate mandatory fields.** After the patch is generated, verify all four
mandatory output dimensions are present. See [Mandatory Output Fields](#mandatory-output-fields).
If the patch output is incomplete, re-generate with more specific instructions.

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
- **Concurrent write operations are locked.** `apply_patch_safe.py --apply` and `run_checks.sh` acquire the repository lock (`.git/deepseek-forge.lock` by default). If the lock is held, stop and tell the user another session is active instead of bypassing it.

## Concurrent Sessions

Multiple Codex conversations may use the installed plugin at the same time.
Different repositories or different git worktrees are safe by default. For the
same worktree, use the repository lock and keep artifacts separated:

```bash
export DEEPSEEK_FORGE_SESSION_ID="codex-$(date +%Y%m%d-%H%M%S)"
```

If the user explicitly sets `DEEPSEEK_FORGE_ARTIFACT_DIR=.deepseek-forge`, warn
that multiple conversations in the same worktree may overwrite artifact files
unless they use different subdirectories.

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
- [ ] **Mandatory fields present:** Affected PRs/files, verification commands, risk points, rollback plan

---

## Reference Documents

Read these files when the situation demands deeper context:

- **`references/workflow.md`**: Detailed workflow steps, examples, and edge case handling. Read when the standard workflow needs clarification or when debugging unexpected behavior.

- **`references/prompt_templates.md`**: The exact prompt templates sent to DeepSeek (`implement_patch`, `fix_tests`, `review_patch`). Read when tweaking template behavior or troubleshooting DeepSeek output quality.

---

## Quick Start Commands

The canonical MVP command sequence. Run all of these from the repository root.

When deepseek-forge is installed as a plugin, use ``${DEEPSEEK_FORGE_HOME}`` to locate the scripts:

```bash
# Prepare the runtime directory
mkdir -p .deepseek-forge

# Step 1: Collect repository context
python3 ${DEEPSEEK_FORGE_HOME}/scripts/collect_context.py \
  --task task.md \
  --output .deepseek-forge/repo_context.md

# Step 2: Generate patch via DeepSeek
python3 ${DEEPSEEK_FORGE_HOME}/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/patch.diff

# Step 3: Validate the patch before applying
python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --check

# Step 4: Apply the validated patch
python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py \
  --patch .deepseek-forge/patch.diff \
  --apply

# Step 5: Run the project's test and lint suite
bash ${DEEPSEEK_FORGE_HOME}/scripts/run_checks.sh
```

**Note:** Runtime artifacts are written to ``DEEPSEEK_FORGE_ARTIFACT_DIR`` (defaults to ``/tmp/deepseek-forge-{pid}/``, or ``/tmp/deepseek-forge-{session}-{pid}/`` when ``DEEPSEEK_FORGE_SESSION_ID`` is set). To keep artifacts in the target repo, set ``DEEPSEEK_FORGE_ARTIFACT_DIR=.deepseek-forge``. If using ``.deepseek-forge/`` as the artifact directory, consider adding it to ``.git/info/exclude``:

```bash
echo '.deepseek-forge/' >> .git/info/exclude
```


---

## Fix Loop Commands

When `run_checks.sh` fails, use this sequence:

```bash
# Generate a fix patch using failure log
python3 ${DEEPSEEK_FORGE_HOME}/scripts/deepseek_worker.py \
  --model deepseek-v4-pro \
  --task task.md \
  --context .deepseek-forge/repo_context.md \
  --output .deepseek-forge/fix.patch.diff \
  --template fix_tests \
  --failure-log .deepseek-forge/check.log

# Validate the fix patch
python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py \
  --patch .deepseek-forge/fix.patch.diff \
  --check

# Apply the fix patch
python3 ${DEEPSEEK_FORGE_HOME}/scripts/apply_patch_safe.py \
  --patch .deepseek-forge/fix.patch.diff \
  --apply

# Re-run checks
bash ${DEEPSEEK_FORGE_HOME}/scripts/run_checks.sh
```
