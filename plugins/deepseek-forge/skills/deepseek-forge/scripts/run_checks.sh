#!/usr/bin/env bash
#
# run_checks.sh - Auto-detect project type and run appropriate checks.
#
# Behavior:
#   1. If CHECK_COMMANDS is set, execute those commands directly.
#   2. Otherwise, detect project type (Node, Python, Go, Rust, Make) and run
#      the relevant test/lint/typecheck commands.
#   3. All output is logged to {artifact_dir}/check.log via tee -a.
#   4. Exits with the first non-zero exit code; prints a summary at the end.
#
# Environment variables:
#   DEEPSEEK_FORGE_ARTIFACT_DIR
#       When set, overrides the directory for check.log (the default is
#       .deepseek-forge/ in the repo root).  Callers may use this to place
#       artifacts under a custom path, though the fix loop normally expects
#       the log inside the target repository.
#   DEEPSEEK_FORGE_LOCK_PATH
#       Optional path to the repository lock directory.
#   DEEPSEEK_FORGE_DISABLE_REPO_LOCK
#       Set to 1 to disable the repository lock.

set -u
set -o pipefail

# ---------------------------------------------------------------------------
# Locate repo root
# ---------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Repository lock
# ---------------------------------------------------------------------------
LOCK_HELD=false
release_repo_lock() {
    if ${LOCK_HELD}; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
        LOCK_HELD=false
    fi
}

if [ "${DEEPSEEK_FORGE_DISABLE_REPO_LOCK:-0}" != "1" ]; then
    if [ -n "${DEEPSEEK_FORGE_LOCK_PATH:-}" ]; then
        LOCK_DIR="$DEEPSEEK_FORGE_LOCK_PATH"
    else
        GIT_DIR="$(git rev-parse --git-dir 2>/dev/null || true)"
        if [ -n "$GIT_DIR" ]; then
            case "$GIT_DIR" in
                /*) LOCK_DIR="$GIT_DIR/deepseek-forge.lock" ;;
                *) LOCK_DIR="$REPO_ROOT/$GIT_DIR/deepseek-forge.lock" ;;
            esac
        else
            LOCK_DIR="$REPO_ROOT/.deepseek-forge/deepseek-forge.lock"
        fi
    fi

    mkdir -p "$(dirname "$LOCK_DIR")"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        LOCK_HELD=true
        {
            echo "pid=$$"
            echo "session=${DEEPSEEK_FORGE_SESSION_ID:-}"
            echo "reason=run_checks.sh"
        } > "$LOCK_DIR/owner"
    else
        echo "ERROR: deepseek-forge repository lock is already held at $LOCK_DIR" >&2
        if [ -f "$LOCK_DIR/owner" ]; then
            cat "$LOCK_DIR/owner" >&2
        fi
        echo "Use a separate git worktree, wait for the other session, or set DEEPSEEK_FORGE_DISABLE_REPO_LOCK=1 if you are sure it is safe." >&2
        exit 1
    fi
fi
trap release_repo_lock EXIT

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
# Default to DEEPSEEK_FORGE_ARTIFACT_DIR; fall back to .deepseek-forge/
# for backward compatibility with the fix loop (which expects check.log
# in the repo so deepseek_worker.py --failure-log can find it).
if [ -n "${DEEPSEEK_FORGE_ARTIFACT_DIR:-}" ]; then
    LOG_DIR="$DEEPSEEK_FORGE_ARTIFACT_DIR"
else
    LOG_DIR=".deepseek-forge"
fi
LOG_FILE="$LOG_DIR/check.log"

mkdir -p "$LOG_DIR"

# Initialise (overwrite, don't append, on a fresh run)
{
    echo "========================================"
    echo "Run Checks started at $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Repository: $REPO_ROOT"
    echo "========================================"
} > "$LOG_FILE"

# ---------------------------------------------------------------------------
# Temp-file tracking / cleanup
# ---------------------------------------------------------------------------
TEMP_FILES=()
cleanup() {
    if [ "${#TEMP_FILES[@]}" -gt 0 ]; then
        for f in "${TEMP_FILES[@]}"; do
            rm -f "$f" 2>/dev/null || true
        done
    fi
    release_repo_lock
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Verbose output mode (env RUN_CHECKS_VERBOSE=1)
# ---------------------------------------------------------------------------
VERBOSE=false
if [ "${RUN_CHECKS_VERBOSE:-0}" = "1" ]; then
    VERBOSE=true
fi

verbose_log() {
    if ${VERBOSE}; then
        echo "[VERBOSE] $*" | tee -a "$LOG_FILE"
    fi
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
FAILED_COMMANDS=()   # human-readable descriptions of what failed
FIRST_FAIL_EXIT=0    # numeric exit code of the first failing check

# ---------------------------------------------------------------------------
# run_check <description> <shell-command>
#
# Executes <shell-command>, streams stdout+stderr to both the terminal and the
# log file.  Records failures so the final summary can report them.
# ---------------------------------------------------------------------------
run_check() {
    local description="$1"
    local cmd="$2"
    local exit_code=0

    echo "" | tee -a "$LOG_FILE"
    echo "--- ${description} ---" | tee -a "$LOG_FILE"
    echo "Command: ${cmd}" | tee -a "$LOG_FILE"

    # Run the command, piping through tee.  Use PIPESTATUS to get the
    # real exit code of eval (not tee, which always succeeds).
    eval "${cmd}" 2>&1 | tee -a "$LOG_FILE"
    exit_code=${PIPESTATUS[0]}

    if [ "${exit_code}" -ne 0 ]; then
        FAILED_COMMANDS+=("${description}")
        if [ "${FIRST_FAIL_EXIT}" -eq 0 ]; then
            FIRST_FAIL_EXIT="${exit_code}"
        fi
        echo "FAILED: ${description} (exit code: ${exit_code})" | tee -a "$LOG_FILE"
        return "${exit_code}"
    fi

    echo "PASSED: ${description}" | tee -a "$LOG_FILE"
    return 0
}

# ---------------------------------------------------------------------------
# Helper: extract a value from package.json scripts using jq (preferred) or
# python3 as a fallback.
# ---------------------------------------------------------------------------
get_npm_script() {
    local script_name="$1"

    if command -v jq &>/dev/null; then
        jq -r ".scripts.${script_name} // empty" package.json 2>/dev/null
    elif command -v python3 &>/dev/null; then
        python3 -c "
import json, sys
try:
    with open('package.json') as f:
        d = json.load(f)
    val = d.get('scripts', {}).get(sys.argv[1], '')
    print(val, end='')
except Exception:
    pass
" "${script_name}" 2>/dev/null
    fi
}

# ============================================================================
# 1.  CHECK_COMMANDS override
# ============================================================================
if [ -n "${CHECK_COMMANDS:-}" ]; then
    echo "" | tee -a "$LOG_FILE"
    echo "=== Executing CHECK_COMMANDS ===" | tee -a "$LOG_FILE"
    echo "Value: ${CHECK_COMMANDS}" | tee -a "$LOG_FILE"

    eval "${CHECK_COMMANDS}" 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=${PIPESTATUS[0]}

    echo "" | tee -a "$LOG_FILE"
    if [ "${EXIT_CODE}" -eq 0 ]; then
        echo "All checks passed" | tee -a "$LOG_FILE"
    else
        echo "Checks failed: CHECK_COMMANDS (exit code: ${EXIT_CODE})" | tee -a "$LOG_FILE"
    fi
    echo "Completed at $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
    exit "${EXIT_CODE}"
fi

# ============================================================================
# 2.  Auto-detection + checks
# ============================================================================
DETECTED=false

# ---- Node.js / JavaScript / TypeScript -----------------------------------
if [ -f "package.json" ]; then
    DETECTED=true
    verbose_log "Node.js detection: found package.json"
    echo "" | tee -a "$LOG_FILE"
    echo "=== Node.js / JavaScript / TypeScript project detected ===" | tee -a "$LOG_FILE"

    has_test="$(get_npm_script test)"
    has_lint="$(get_npm_script lint)"
    has_typecheck="$(get_npm_script typecheck)"

    any_found=false

    if [ -n "${has_test}" ]; then
        run_check "npm test"            "npm test"
        any_found=true
    fi

    if [ -n "${has_lint}" ]; then
        run_check "npm run lint"        "npm run lint"
        any_found=true
    fi

    if [ -n "${has_typecheck}" ]; then
        run_check "npm run typecheck"   "npm run typecheck"
        any_found=true
    elif [ -f "tsconfig.json" ]; then
        run_check "TypeScript type check" "npx tsc --noEmit"
        any_found=true
    fi

    if ! ${any_found}; then
        echo "No npm check scripts detected" | tee -a "$LOG_FILE"
    fi
fi

# ---- Python ----------------------------------------------------------------
if [ -f "pyproject.toml" ] || [ -f "setup.py" ] || [ -f "setup.cfg" ] || [ -f "requirements.txt" ]; then
    DETECTED=true
    verbose_log "Python detection: found config file(s) (pyproject.toml, setup.py, setup.cfg, or requirements.txt)"
    echo "" | tee -a "$LOG_FILE"
    echo "=== Python project detected ===" | tee -a "$LOG_FILE"

    # Detect pytest configuration
    has_pytest=false
    if [ -f "pytest.ini" ] || [ -f "conftest.py" ]; then
        has_pytest=true
    elif [ -f "pyproject.toml" ] && grep -Fq '[tool.pytest]' pyproject.toml 2>/dev/null; then
        has_pytest=true
    fi

    # Look for unittest-style test files
    test_files="$(find . \
        -not -path '*/node_modules/*' \
        -not -path '*/.git/*' \
        -not -path '*/__pycache__/*' \
        -not -path '*/venv/*' \
        -not -path '*/.venv/*' \
        \( -name 'test_*.py' -o -name '*_test.py' \) \
        -print -quit 2>/dev/null)"

    if [ -z "${test_files}" ] && ! ${has_pytest}; then
        echo "No Python test framework detected" | tee -a "$LOG_FILE"
    else
        if [ -n "${test_files}" ]; then
            run_check "Python unittest"  "python3 -m unittest discover"
        fi
        if ${has_pytest}; then
            run_check "Python pytest"    "python3 -m pytest"
        fi
    fi
fi

# ---- Go --------------------------------------------------------------------
if [ -f "go.mod" ]; then
    DETECTED=true
    verbose_log "Go detection: found go.mod"
    echo "" | tee -a "$LOG_FILE"
    echo "=== Go project detected ===" | tee -a "$LOG_FILE"
    run_check "Go tests" "go test ./..."
fi

# ---- Rust ------------------------------------------------------------------
if [ -f "Cargo.toml" ]; then
    DETECTED=true
    verbose_log "Rust detection: found Cargo.toml"
    echo "" | tee -a "$LOG_FILE"
    echo "=== Rust project detected ===" | tee -a "$LOG_FILE"
    run_check "Rust tests" "cargo test"
fi

# ---- Make ------------------------------------------------------------------
if [ -f "Makefile" ]; then
    if grep -qE '^test:' Makefile 2>/dev/null; then
        DETECTED=true
        verbose_log "Make detection: found Makefile with test target"
        echo "" | tee -a "$LOG_FILE"
        echo "=== Make test target detected ===" | tee -a "$LOG_FILE"
        run_check "make test" "make test"
    fi
fi

# ---- Python (test-file fallback, no config files) ---------------------------
if ! ${DETECTED}; then
    verbose_log "Python fallback: searching for test files (no config files found)"

    # Search for Python test files anywhere in the repo
    py_test_files="$(find . \
        -not -path '*/node_modules/*' \
        -not -path '*/.git/*' \
        -not -path '*/__pycache__/*' \
        -not -path '*/venv/*' \
        -not -path '*/.venv/*' \
        \( -name 'test_*.py' -o -name '*_test.py' \) \
        -print -quit 2>/dev/null)"

    if [ -n "${py_test_files}" ]; then
        DETECTED=true
        verbose_log "Python fallback: detected via test files (e.g. ${py_test_files})"
        echo "" | tee -a "$LOG_FILE"
        echo "=== Python project detected (test-file fallback) ===" | tee -a "$LOG_FILE"

        run_check "Python unittest"  "python3 -m unittest discover"

        if command -v python3 &>/dev/null && python3 -c "import pytest" 2>/dev/null; then
            run_check "Python pytest"    "python3 -m pytest"
        fi
    else
        verbose_log "Python fallback: no test files found"
    fi
fi

# ============================================================================
# 3.  No project type detected
# ============================================================================
if ! ${DETECTED}; then
    echo "No recognized project type detected" | tee -a "$LOG_FILE"
    echo "Completed at $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
    exit 0
fi

# ============================================================================
# 4.  Summary
# ============================================================================
echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

if [ "${#FAILED_COMMANDS[@]}" -eq 0 ]; then
    echo "All checks passed" | tee -a "$LOG_FILE"
else
    echo "Checks failed: ${FAILED_COMMANDS[*]}" | tee -a "$LOG_FILE"
fi

echo "Completed at $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"

exit "${FIRST_FAIL_EXIT}"
