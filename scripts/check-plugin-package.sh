#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="${REPO_ROOT}/plugins/deepseek-forge"
MARKETPLACE_JSON="${REPO_ROOT}/.agents/plugins/marketplace.json"
PLUGIN_JSON="${PLUGIN_DIR}/.codex-plugin/plugin.json"
MCP_JSON="${PLUGIN_DIR}/.mcp.json"
FAILURES=0
CLEAN_PACKAGE=0

usage() {
  cat <<'EOF'
Usage: scripts/check-plugin-package.sh [--clean]

Validate the local Codex plugin package layout.

Options:
      --clean  Remove plugin __pycache__ / .pyc files before checking.
  -h, --help   Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN_PACKAGE=1
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

ok() {
  echo "[check-plugin-package] OK: $*"
}

fail() {
  echo "[check-plugin-package] ERROR: $*" >&2
  FAILURES=$((FAILURES + 1))
}

require_file() {
  if [[ -f "$1" ]]; then
    ok "found ${1#${REPO_ROOT}/}"
  else
    fail "missing ${1#${REPO_ROOT}/}"
  fi
}

json_check() {
  if python3 -m json.tool "$1" >/dev/null; then
    ok "valid JSON: ${1#${REPO_ROOT}/}"
  else
    fail "invalid JSON: ${1#${REPO_ROOT}/}"
  fi
}

clean_bytecode_artifacts() {
  if [[ "${CLEAN_PACKAGE}" -ne 1 ]]; then
    return
  fi

  find "${PLUGIN_DIR}" -type d -name '__pycache__' -prune -exec rm -rf {} +
  find "${PLUGIN_DIR}" -type f -name '*.pyc' -exec rm -f {} +
  ok "cleaned plugin __pycache__ and .pyc files"
}

clean_bytecode_artifacts

require_file "${MARKETPLACE_JSON}"
require_file "${PLUGIN_JSON}"
require_file "${MCP_JSON}"

json_check "${MARKETPLACE_JSON}"
json_check "${PLUGIN_JSON}"
json_check "${MCP_JSON}"

if [[ "${FAILURES}" -ne 0 ]]; then
  echo "[check-plugin-package] ${FAILURES} basic check(s) failed." >&2
  exit 1
fi

MARKETPLACE_PATH="$(python3 - "${MARKETPLACE_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
print(data["plugins"][0]["source"]["path"])
PY
)"

if [[ "${MARKETPLACE_PATH}" == "./plugins/deepseek-forge" ]]; then
  ok "marketplace path points at ./plugins/deepseek-forge"
else
  fail "marketplace path should be ./plugins/deepseek-forge, got ${MARKETPLACE_PATH}"
fi

PLUGIN_VERSION="$(python3 - "${PLUGIN_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
print(data["version"])
PY
)"

PLUGIN_LAYOUT="$(python3 - "${PLUGIN_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
print(f"{data.get('skills')}|{data.get('mcpServers')}")
PY
)"

if [[ "${PLUGIN_LAYOUT}" == "skills|.mcp.json" ]]; then
  ok "plugin manifest points at skills/ and .mcp.json"
else
  fail "plugin manifest layout should be skills|.mcp.json, got ${PLUGIN_LAYOUT}"
fi

for forbidden in ".codex-plugin" ".mcp.json" "skills" "mcp"; do
  if [[ -e "${REPO_ROOT}/${forbidden}" ]]; then
    fail "root-level ${forbidden} must not exist; keep runtime files under plugins/deepseek-forge/"
  else
    ok "root-level ${forbidden} absent"
  fi
done

BYTECODE_ARTIFACTS="$(find "${PLUGIN_DIR}" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \) -print)"
if [[ -n "${BYTECODE_ARTIFACTS}" ]]; then
  fail "plugin package contains Python bytecode artifacts"
  echo "${BYTECODE_ARTIFACTS}" >&2
else
  ok "plugin package contains no __pycache__ or .pyc files"
fi

if python3 - "${PLUGIN_VERSION}" "${REPO_ROOT}/README.md" "${REPO_ROOT}/README.zh-CN.md" <<'PY'
import re
import sys

version = sys.argv[1]
ok = True
pattern = re.compile(r"version-v([0-9]+\.[0-9]+\.[0-9]+)-blue")
for path in sys.argv[2:]:
    text = open(path, encoding="utf-8").read()
    match = pattern.search(text)
    if not match:
        print(f"missing version badge: {path}", file=sys.stderr)
        ok = False
    elif match.group(1) != version:
        print(
            f"badge version mismatch in {path}: {match.group(1)} != {version}",
            file=sys.stderr,
        )
        ok = False
sys.exit(0 if ok else 1)
PY
then
  ok "README version badges match plugin version ${PLUGIN_VERSION}"
else
  fail "README version badges must match plugin version ${PLUGIN_VERSION}"
fi

if [[ "${FAILURES}" -ne 0 ]]; then
  echo "[check-plugin-package] ${FAILURES} check(s) failed." >&2
  exit 1
fi

echo "[check-plugin-package] All checks passed for deepseek-forge ${PLUGIN_VERSION}."
