# Codex-DeepSeek Forge Workflow Reference

## Overview

The orchestrator enables a structured collaboration between Codex and DeepSeek: Codex plans the work, DeepSeek generates implementation patches as unified diffs, and Codex validates, applies, and verifies those patches. **Codex is the sole executor** -- it owns the repository, runs all commands, and makes all decisions. DeepSeek operates as a **diff-only interface**: it reads context, reasons about changes, and outputs nothing but a unified diff patch. DeepSeek never touches the filesystem, never runs a command, and never interacts with git.

This separation of concerns ensures safety (Codex gates all side effects) while leveraging each model's strengths: Codex for high-level reasoning and orchestration, DeepSeek for focused, low-level code generation.

---

## Standard Workflow

The standard workflow has 7 steps. Each step is executed sequentially; Codex should not proceed to the next step until the current one succeeds.

### Step 1: Plan

Codex reads the task description and creates a plan file.

```bash
mkdir -p .deepseek-forge
# Codex writes the plan based on the task
# Output: .deepseek-forge/plan.md
```

The plan should describe:
- What files need to change
- What functions or classes are affected
- The expected behavior after the change
- Any edge cases or constraints

### Step 2: Collect Context

Run the context collection script to gather the relevant source files, imports, and dependencies that DeepSeek will need to see.

```bash
python scripts/collect_context.py \
  --plan .deepseek-forge/plan.md \
  --output .deepseek-forge/context.txt
```

This produces a consolidated context file containing:
- The plan
- Relevant source files (filtered by the plan's file list)
- Import graphs for the affected modules
- Any test files related to the changed modules

### Step 3: Generate Patch

Send the context to DeepSeek using the `implement_patch` template to generate a unified diff.

```bash
python scripts/deepseek_worker.py \
  --template implement_patch \
  --context .deepseek-forge/context.txt \
  --output .deepseek-forge/patch.diff
```

DeepSeek returns a raw unified diff. The worker script handles the API communication and writes the diff to the output file. DeepSeek itself never sees or touches the filesystem.

### Step 4: Validate the Patch

Run the safety checker on the generated patch before applying it.

```bash
python scripts/apply_patch_safe.py --check .deepseek-forge/patch.diff
```

The safety checker validates:
- Patch can be parsed as a valid unified diff
- All file paths exist in the repository (or are new files)
- No deleted files without explicit Codex confirmation
- No shell commands embedded in the diff
- No git operations embedded in the diff
- Diff hunks apply cleanly without conflicts (dry-run)

If validation fails, the patch is rejected. Codex must review the failure and decide whether to retry generation or fix the issue manually.

### Step 5: Review

Codex reviews the patch for correctness and safety before application. This is a human/Codex judgment step, not an automated one.

Codex should check:
- Does the change implement the plan correctly?
- Are there any unexpected side effects?
- Are edge cases handled?
- Is the code style consistent with the codebase?
- Are there any security concerns (e.g., eval, exec, unsanitized input)?

If Codex rejects the patch, return to Step 3 with a refined plan or more specific instructions.

### Step 6: Apply

Apply the validated and reviewed patch to the working tree.

```bash
python scripts/apply_patch_safe.py --apply .deepseek-forge/patch.diff
```

This modifies the actual repository files. The `--apply` flag must be explicitly passed; the script will not apply without it. After application, Codex should run `git diff` to inspect the actual changes on disk.

### Step 7: Verify

Run the project's checks to verify the patch doesn't break anything.

```bash
bash scripts/run_checks.sh
```

This script should run:
- Linter (e.g., ruff, pylint, eslint)
- Type checker (e.g., mypy, pyright, tsc)
- Test suite (e.g., pytest, jest)
- Any project-specific checks (e.g., build verification)

If all checks pass with zero failures, the workflow is complete. Codex may then do a final review and, if appropriate, commit the changes.

### Complete Example

```bash
# Step 1: Codex writes the plan
mkdir -p .deepseek-forge
cat > .deepseek-forge/plan.md << 'PLAN'
# Task: Add input validation to user_service.py
## Files to change
- src/services/user_service.py: add email validation
- tests/test_user_service.py: add validation test cases
## Expected behavior
- create_user() rejects invalid email addresses
- Raises ValueError with descriptive message
PLAN

# Step 2: Collect context
python scripts/collect_context.py \
  --plan .deepseek-forge/plan.md \
  --output .deepseek-forge/context.txt

# Step 3: Generate patch
python scripts/deepseek_worker.py \
  --template implement_patch \
  --context .deepseek-forge/context.txt \
  --output .deepseek-forge/patch.diff

# Step 4: Validate
python scripts/apply_patch_safe.py --check .deepseek-forge/patch.diff

# Step 5: Codex reviews the patch manually
# (read the diff, verify correctness)

# Step 6: Apply
python scripts/apply_patch_safe.py --apply .deepseek-forge/patch.diff

# Step 7: Verify
bash scripts/run_checks.sh
```

---

## Fix Loop (Failure Recovery)

When Step 7 (`run_checks.sh`) fails, the orchestrator enters a **fix loop**. This loop attempts to automatically diagnose and correct the failure, up to a maximum of **3 attempts**.

### Fix Loop Procedure

**Attempt N (1 through 3):**

1. **Capture the failure log** from the failed check run:

   ```bash
   bash scripts/run_checks.sh 2>&1 | tee .deepseek-forge/failure_${N}.log
   ```

2. **Generate a fix patch** using the `fix_tests` template, providing the failure log:

   ```bash
   python scripts/deepseek_worker.py \
     --template fix_tests \
     --context .deepseek-forge/context.txt \
     --failure-log .deepseek-forge/failure_${N}.log \
     --output .deepseek-forge/fix_${N}.diff
   ```

3. **Validate the fix patch:**

   ```bash
   python scripts/apply_patch_safe.py --check .deepseek-forge/fix_${N}.diff
   ```

4. **Apply the fix patch:**

   ```bash
   python scripts/apply_patch_safe.py --apply .deepseek-forge/fix_${N}.diff
   ```

5. **Re-run checks:**

   ```bash
   bash scripts/run_checks.sh 2>&1 | tee .deepseek-forge/failure_${N}.log
   ```

6. **Evaluate the result:**
   - If checks pass: fix loop succeeds, exit the loop.
   - If checks fail and N < 3: increment N, go back to step 2.
   - If checks fail and N = 3: **stop and report failure**. All fix attempts have been exhausted.

### Fix Loop Rules

- Only the `fix_tests` template is used during the fix loop; never re-run `implement_patch`.
- Each fix attempt gets its own numbered files (`fix_1.diff`, `failure_1.log`, etc.) for traceability.
- The failure log from the **previous** run is used as input; do not accumulate logs across attempts.
- If the patch validator rejects a fix patch at step 3, that counts as a failed attempt.
- After 3 failed attempts, Codex must inspect the accumulated failure logs and decide on a manual resolution strategy.

### Fix Loop Example

```bash
# Attempt 1
bash scripts/run_checks.sh 2>&1 | tee .deepseek-forge/failure_1.log
python scripts/deepseek_worker.py \
  --template fix_tests \
  --context .deepseek-forge/context.txt \
  --failure-log .deepseek-forge/failure_1.log \
  --output .deepseek-forge/fix_1.diff
python scripts/apply_patch_safe.py --check .deepseek-forge/fix_1.diff
python scripts/apply_patch_safe.py --apply .deepseek-forge/fix_1.diff
bash scripts/run_checks.sh 2>&1 | tee .deepseek-forge/failure_1.log
# If still failing...

# Attempt 2
python scripts/deepseek_worker.py \
  --template fix_tests \
  --context .deepseek-forge/context.txt \
  --failure-log .deepseek-forge/failure_1.log \
  --output .deepseek-forge/fix_2.diff
# ... (validate, apply, re-check)
```

---

## Safety Rules

These rules are hard constraints. Violating any of them invalidates the orchestrator's security model.

### DeepSeek Prohibitions

1. **DeepSeek must never execute shell commands.** DeepSeek has no shell access and must never suggest shell commands in its output. Codex is the only agent permitted to run commands.

2. **DeepSeek must never git commit.** DeepSeek must not reference `git commit`, `git add`, `git push`, or any other git mutating operation. Codex alone decides when and what to commit.

3. **DeepSeek must never delete files.** If a change requires deleting a file, DeepSeek must note this as a comment within the diff header (e.g., `# NOTE: This file should be deleted`). Codex must explicitly confirm and perform the deletion. DeepSeek must never include a full-file deletion diff.

4. **DeepSeek must never create executable scripts.** Any new shell scripts or executable files must be flagged for Codex review. DeepSeek may propose the content but must not mark files as executable in the diff.

### Codex Responsibilities

1. **Codex is the only agent that applies patches.** The `apply_patch_safe.py` script is the sole mechanism for modifying repository files. Codex must never blindly apply a patch without review.

2. **Codex is the only agent that runs commands.** All shell execution (tests, linters, git, build tools) goes through Codex.

3. **Codex is the only agent that modifies the repository.** This includes git operations, file creation, file deletion, and permission changes.

4. **All patches must pass `apply_patch_safe.py --check` before application.** This is a non-negotiable gate. If `--check` fails, the patch must be discarded or regenerated.

5. **Codex must review every patch before applying.** Even if `--check` passes (syntactic validation), Codex must perform semantic review. Codex can reject a patch that is syntactically valid but logically wrong.

---

## Exit Conditions

### Success

The workflow exits successfully when:
- All 7 standard workflow steps complete without error, OR
- The fix loop resolves all failures within 3 attempts
- All checks pass (`run_checks.sh` exits with code 0)
- Codex performs a final review and confirms the changes are correct

At this point, Codex may proceed to commit, create a PR, or take any other appropriate action.

### Failure

The workflow exits with failure when:
- 3 consecutive fix attempts all fail (checks still do not pass)
- At this point, Codex should:
  - Summarize the accumulated failure logs
  - Report which tests or checks are failing
  - Recommend a manual investigation path
  - Leave the repository in its last known state (with the most recent fix attempt applied)

### Rejection

The workflow exits with rejection when:
- `apply_patch_safe.py --check` rejects a patch (syntactic or safety violation)
- Codex rejects a patch during manual review (semantic concern)
- In either case, the rejection reason must be recorded in `.deepseek-forge/rejection.md`
- Codex may decide to:
  - Refine the plan and re-run from Step 3
  - Manually implement the change instead
  - Abandon the task

---

## Directory Conventions

All runtime artifacts produced by the orchestrator live under `.deepseek-forge/` at the repository root. This directory is gitignored and must never be committed.

| Path | Purpose |
|------|---------|
| `.deepseek-forge/plan.md` | Task plan written by Codex |
| `.deepseek-forge/context.txt` | Collected repository context |
| `.deepseek-forge/patch.diff` | Primary implementation patch |
| `.deepseek-forge/fix_1.diff` | First fix attempt patch |
| `.deepseek-forge/fix_2.diff` | Second fix attempt patch |
| `.deepseek-forge/fix_3.diff` | Third fix attempt patch |
| `.deepseek-forge/failure_1.log` | Failure log from first check run |
| `.deepseek-forge/failure_2.log` | Failure log from second check run |
| `.deepseek-forge/failure_3.log` | Failure log from third check run |
| `.deepseek-forge/rejection.md` | Rejection reason (if applicable) |

The `.deepseek-forge/` directory is created by Step 1 and populated throughout the workflow. It can be safely deleted at any time to reset the orchestrator state.
