#!/usr/bin/env bash
set -euo pipefail

MARKETPLACE_NAME="${MARKETPLACE_NAME:-deepseek-forge}"
PLUGIN_SELECTOR="${PLUGIN_SELECTOR:-deepseek-forge@deepseek-forge}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="${REPO_ROOT}/plugins/deepseek-forge"
PLUGIN_JSON="${PLUGIN_DIR}/.codex-plugin/plugin.json"
CODEX_HOME_DIR="${CODEX_HOME:-${HOME}/.codex}"
DRY_RUN=0
CLEAN_PACKAGE=1

usage() {
  cat <<'EOF'
Usage: scripts/reinstall-local-plugin.sh [--dry-run] [--no-clean]

Reinstall the local deepseek-forge Codex plugin into CODEX_HOME.

Options:
  -n, --dry-run   Print planned actions without changing files or Codex config.
      --no-clean  Do not remove __pycache__ / .pyc files before installation.
  -h, --help      Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run)
      DRY_RUN=1
      ;;
    --no-clean)
      CLEAN_PACKAGE=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "${PLUGIN_SELECTOR}" == *@* ]]; then
  PLUGIN_NAME="${PLUGIN_SELECTOR#*@}"
else
  PLUGIN_NAME="${PLUGIN_SELECTOR}"
fi

PLUGIN_VERSION="$(python3 - "${PLUGIN_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f)["version"])
PY
)"

run() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

check_codex_cli() {
  local output
  if ! output="$(codex --version 2>&1)"; then
    cat >&2 <<EOF
[deepseek-forge] ERROR: Codex CLI appears to be broken before plugin installation.
[deepseek-forge] DeepSeek Forge has not been loaded yet, so this is usually not a plugin package error.
[deepseek-forge] codex --version output:
${output}
[deepseek-forge] Try reinstalling Codex CLI with optional native dependencies:
  npm install -g @openai/codex@latest --force --include=optional
EOF
    exit 1
  fi

  echo "[deepseek-forge] Codex CLI: ${output}"
}

warn_if_dirty() {
  if git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local status
    status="$(git -C "${REPO_ROOT}" status --short --untracked-files=all)"
    if [[ -n "${status}" ]]; then
      echo "[deepseek-forge] WARNING: worktree has uncommitted changes." >&2
      echo "${status}" >&2
    fi
  fi
}

clean_package_artifacts() {
  if [[ "${CLEAN_PACKAGE}" -eq 0 ]]; then
    echo "[deepseek-forge] Skipping package artifact cleanup (--no-clean)."
    return
  fi

  local artifacts
  artifacts="$(find "${PLUGIN_DIR}" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \) -print)"
  if [[ -z "${artifacts}" ]]; then
    echo "[deepseek-forge] Package artifact cleanup: no __pycache__ or .pyc files found."
    return
  fi

  echo "[deepseek-forge] Package artifact cleanup will remove:"
  echo "${artifacts}" | sed 's/^/  /'

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    return
  fi

  find "${PLUGIN_DIR}" -type d -name '__pycache__' -prune -exec rm -rf {} +
  find "${PLUGIN_DIR}" -type f -name '*.pyc' -exec rm -f {} +
}

echo "[deepseek-forge] Marketplace root: ${REPO_ROOT}"
echo "[deepseek-forge] Plugin selector: ${PLUGIN_SELECTOR}"
echo "[deepseek-forge] Plugin version: ${PLUGIN_VERSION}"
echo "[deepseek-forge] CODEX_HOME: ${CODEX_HOME_DIR}"

warn_if_dirty
clean_package_artifacts

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "[deepseek-forge] Dry run complete. No files or Codex config were changed."
  echo "[deepseek-forge] Would run package checks and reinstall the plugin."
  exit 0
fi

check_codex_cli

"${REPO_ROOT}/scripts/check-plugin-package.sh"

# Re-point the local marketplace to this checkout. This keeps development
# worktrees from accidentally reinstalling an older checkout registered earlier.
run codex plugin marketplace remove "${MARKETPLACE_NAME}" >/dev/null 2>&1 || true
run codex plugin marketplace add "${REPO_ROOT}"

# Reinstall so Codex copies the current plugin package into its versioned cache.
run codex plugin remove "${PLUGIN_SELECTOR}" >/dev/null 2>&1 || true
run codex plugin add "${PLUGIN_SELECTOR}"

run codex plugin list | grep -F "${PLUGIN_SELECTOR}" || true

CACHE_PLUGIN_JSON="${CODEX_HOME_DIR}/plugins/cache/${MARKETPLACE_NAME}/${PLUGIN_NAME}/${PLUGIN_VERSION}/.codex-plugin/plugin.json"
if [[ ! -f "${CACHE_PLUGIN_JSON}" ]]; then
  echo "[deepseek-forge] ERROR: expected cache manifest not found: ${CACHE_PLUGIN_JSON}" >&2
  exit 1
fi

CACHE_VERSION="$(python3 - "${CACHE_PLUGIN_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f)["version"])
PY
)"

if [[ "${CACHE_VERSION}" != "${PLUGIN_VERSION}" ]]; then
  echo "[deepseek-forge] ERROR: cache version ${CACHE_VERSION} != plugin version ${PLUGIN_VERSION}" >&2
  exit 1
fi

CACHE_PLUGIN_DIR="$(dirname "$(dirname "${CACHE_PLUGIN_JSON}")")"
CACHE_ARTIFACTS="$(find "${CACHE_PLUGIN_DIR}" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \) -print)"
if [[ -n "${CACHE_ARTIFACTS}" ]]; then
  echo "[deepseek-forge] ERROR: cache contains Python bytecode artifacts:" >&2
  echo "${CACHE_ARTIFACTS}" >&2
  exit 1
fi

echo "[deepseek-forge] Verified cache version: ${CACHE_VERSION}"

echo "[deepseek-forge] Reinstall complete. Start a new Codex session to load updated skills and MCP definitions."
