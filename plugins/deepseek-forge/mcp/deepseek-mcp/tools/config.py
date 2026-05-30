"""Shared configuration and API helpers for deepseek-forge-mcp tools."""

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Internal defaults — not exposed as user-facing config
_DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions"
_DEFAULT_TEMPERATURE = 0.2
_DEFAULT_TIMEOUT = 120

# Reasoning effort: canonical values and compatibility mappings
_VALID_REASONING_EFFORTS = {"high", "max"}
_REASONING_EFFORT_MAP = {
    "low": "high",
    "medium": "high",
    "xhigh": "max",
}

# Model name patterns that support reasoning_effort parameter
_REASONING_MODEL_PATTERNS = ("v4-pro", "reasoner")


def _sanitize_path_component(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    sanitized = sanitized.strip(".-")
    return sanitized or "session"


def get_session_id() -> str | None:
    raw = os.environ.get("DEEPSEEK_FORGE_SESSION_ID")
    if not raw:
        return None
    return _sanitize_path_component(raw)


# ---------------------------------------------------------------------------
# Artifact directory — same isolation logic as scripts/forge_config.py so that
# MCP tools and the dev loop share the same artifact tree.
# ---------------------------------------------------------------------------


def _find_git_root() -> Path | None:
    d = Path.cwd().resolve()
    while True:
        if (d / ".git").exists():
            return d
        parent = d.parent
        if parent == d:
            return None
        d = parent


def _repo_hash() -> str:
    root = _find_git_root() or Path.cwd().resolve()
    return hashlib.sha256(str(root).encode()).hexdigest()[:12]


def _thread_id() -> str:
    tid = os.environ.get("CODEX_THREAD_ID", "").strip()
    if tid:
        return tid
    sid = get_session_id()
    if sid:
        return sid
    return f"unknown-{os.getpid()}"


def _run_id() -> str:
    rid = os.environ.get("DEEPSEEK_FORGE_RUN_ID", "").strip()
    if rid:
        return rid
    return f"run-{int(time.time())}"


def get_artifact_dir() -> Path:
    """Return the isolated artifact directory (mirrors forge_config.get_artifact_dir)."""
    repo_local = os.environ.get("DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS", "false")
    if repo_local.lower() in ("true", "1"):
        root = _find_git_root() or Path.cwd().resolve()
        base = root / ".deepseek-forge"
    else:
        env = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
        if env:
            base = Path(env).expanduser().resolve()
        else:
            base = Path("/tmp/deepseek-forge")

    return base / _repo_hash() / _thread_id() / _run_id()


# ---------------------------------------------------------------------------
# Shared response extraction helpers (used by all MCP tools)
# ---------------------------------------------------------------------------


def _validate_diff(diff_text: str) -> None:
    """Raise ValueError if *diff_text* is not a plausible unified diff."""
    if not diff_text or not diff_text.strip():
        raise ValueError("Response contains no content (empty)")

    has_src = diff_text.strip().startswith("--- ")
    has_dst = "+++ b/" in diff_text
    has_hunk = "@@" in diff_text and " @@" in diff_text

    if not has_src:
        raise ValueError("Response does not start with '--- ' source header")
    if not has_dst:
        raise ValueError("Response missing '+++ b/' destination header")
    if not has_hunk:
        raise ValueError("Response missing '@@ ... @@' hunk header")

    for line in diff_text.split("\n"):
        stripped = line.strip()
        if stripped in ("$ ", "$", "> ", "bash", "#!/bin/bash", "#!/bin/sh"):
            raise ValueError(
                f"Response contains prohibited shell content: '{stripped}'"
            )
        if "git add" in stripped or "git commit" in stripped or "git push" in stripped:
            raise ValueError(f"Response contains prohibited git command: '{stripped}'")


def extract_diff(response_text: str) -> str:
    """Extract a validated unified diff from a model response.

    Strips markdown code fences, locates the diff block, validates its
    structure, and warns on stderr about stripped non-diff lines.
    Raises ``ValueError`` if no valid diff is found.
    """
    text = response_text.strip()

    if text.startswith("```diff"):
        print(
            "[deepseek-forge-mcp] Warning: removing diff code fences from response",
            file=sys.stderr,
        )
        text = text[len("```diff"):].strip()
    elif text.startswith("```"):
        print(
            "[deepseek-forge-mcp] Warning: removing code fences from response",
            file=sys.stderr,
        )
        text = text[len("```"):].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    lines = text.split("\n")
    diff_start = None
    diff_end = None
    diff_prefixes = ("+", "-", " ", "@@", "---", "+++", "\\")

    for i, line in enumerate(lines):
        if diff_start is None and (
            line.startswith("--- a/") or line.startswith("--- /dev/null")
        ):
            diff_start = i
        if line and any(line.startswith(p) for p in diff_prefixes):
            diff_end = i

    if diff_start is None or diff_end is None or diff_end < diff_start:
        raise ValueError("Response contains no valid unified diff")

    stripped_before = diff_start
    stripped_after = len(lines) - 1 - diff_end
    total_stripped = stripped_before + stripped_after
    if total_stripped > 0:
        print(
            f"[deepseek-forge-mcp] Warning: stripped {total_stripped} non-diff "
            f"lines from response",
            file=sys.stderr,
        )

    diff_text = "\n".join(lines[diff_start:diff_end + 1])
    _validate_diff(diff_text)
    return diff_text


def extract_json(response_text: str) -> dict:
    """Extract a JSON object from a model response (handles markdown fences)."""
    import json as _json
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```"):
        text = text[len("```"):].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return _json.loads(text)


def get_artifact_path(filename: str) -> str:
    """Return a default artifact path for *filename*."""
    return str(get_artifact_dir() / filename)


def validate_output_path(output_path: str) -> str:
    """Validate and return a safe output path, raising ValueError on traversal.

    Rejects absolute paths and paths containing ``..`` segments.
    Returns the resolved path under the artifact directory if the requested
    path is relative and safe.
    """
    if os.path.isabs(output_path):
        raise ValueError(f"Absolute output path rejected: {output_path}")
    segments = output_path.replace("\\", "/").split("/")
    if ".." in segments:
        raise ValueError(f"Path traversal rejected in output path: {output_path}")
    return output_path


def ensure_output_dir(output_path: str) -> None:
    """Create parent directories for *output_path* if they don't exist."""
    d = os.path.dirname(output_path)
    if d:
        os.makedirs(d, exist_ok=True)


def read_template(template_name: str) -> str:
    tool_dir = os.path.dirname(os.path.abspath(__file__))

    search_paths = []

    # Undocumented override: check env var first so it takes priority
    env_path = os.environ.get("DEEPSEEK_TEMPLATE_PATH")
    if env_path:
        search_paths.append(env_path)

    search_paths.extend([
        os.path.join(tool_dir, "..", "..", "..", "skills", "deepseek-forge", "references", "prompt_templates.md"),
        os.path.join(tool_dir, "..", "..", "references", "prompt_templates.md"),
    ])

    tmpl_path = None
    for p in search_paths:
        if os.path.exists(p):
            tmpl_path = p
            break

    if tmpl_path is None:
        raise FileNotFoundError(
            f"Template file not found. Searched: {', '.join(search_paths)}"
        )

    with open(tmpl_path) as f:
        content = f.read()

    escaped = re.escape(template_name)
    heading_pat = re.compile(
        r"^#{2,4}\s+Template:\s+`?" + escaped + r"`?", re.MULTILINE
    )
    match = heading_pat.search(content)
    if match is None:
        raise ValueError(f"Template '{template_name}' not found in {tmpl_path}")

    start = match.end()
    if start < len(content) and content[start] == "\n":
        start += 1

    next_heading = re.compile(r"^#{2,4}\s+Template:", re.MULTILINE)
    next_match = next_heading.search(content, start)
    end = next_match.start() if next_match else len(content)
    return content[start:end].strip()


def _normalize_reasoning_effort(raw: str) -> str:
    """Validate and normalize the reasoning_effort value.

    Canonical values: ``high``, ``max``.
    Compat mappings: ``low`` / ``medium`` → ``high``, ``xhigh`` → ``max``.
    Raises ValueError for unrecognized input.
    """
    if raw in _VALID_REASONING_EFFORTS:
        return raw
    mapped = _REASONING_EFFORT_MAP.get(raw)
    if mapped is not None:
        return mapped
    raise ValueError(
        f"Invalid DEEPSEEK_REASONING_EFFORT: '{raw}'. "
        f"Valid values: {', '.join(sorted(_VALID_REASONING_EFFORTS))}"
    )


def _model_supports_reasoning(model: str) -> bool:
    """Return True if *model* is known to accept a ``reasoning_effort`` parameter."""
    return any(pattern in model for pattern in _REASONING_MODEL_PATTERNS)


def get_config():
    """Return resolved configuration from environment variables.

    Public env vars (documented for users):
      - DEEPSEEK_API_KEY (required)
      - DEEPSEEK_MODEL (default: deepseek-v4-pro)
      - DEEPSEEK_REASONING_EFFORT (default: max)
      - DEEPSEEK_ENABLE_1M_CONTEXT (default: true)
      - DEEPSEEK_FORGE_ARTIFACT_DIR (optional artifact directory)
      - DEEPSEEK_FORGE_SESSION_ID (optional default artifact namespace)

    Internal defaults (not in user docs):
      - endpoint, temperature, timeout
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY environment variable is not set")

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")

    raw_effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "max")
    reasoning_effort = _normalize_reasoning_effort(raw_effort)

    enable_1m_str = os.environ.get("DEEPSEEK_ENABLE_1M_CONTEXT", "true")
    enable_1m = enable_1m_str.lower() in ("true", "1")

    return {
        "api_key": api_key,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "enable_1m_context": enable_1m,
        # Internal defaults
        "endpoint": _DEFAULT_ENDPOINT,
        "temperature": _DEFAULT_TEMPERATURE,
        "timeout": _DEFAULT_TIMEOUT,
    }


def build_request_body(model, messages, reasoning_effort=None):
    """Build the JSON-serialisable API request body.

    Always includes: model, messages, temperature.
    Includes reasoning_effort only when the model supports it.
    """
    body = {
        "model": model,
        "messages": messages,
        "temperature": _DEFAULT_TEMPERATURE,
    }

    if reasoning_effort and _model_supports_reasoning(model):
        body["reasoning_effort"] = reasoning_effort

    return body


def call_api(endpoint, api_key, request_body, timeout):
    """POST *request_body* to the DeepSeek API, return the assistant's text content."""
    data = json.dumps(request_body).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"DeepSeek API HTTP {e.code}: {body[:500]}"
        )
    except (TimeoutError, OSError) as e:
        raise RuntimeError(f"DeepSeek API request failed: {e}")
