"""Shared configuration and API helpers for deepseek-forge-mcp tools."""

import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request

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


def get_artifact_dir() -> str:
    """Return the artifact directory used by MCP tools."""
    env = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))

    session_id = get_session_id()
    if session_id:
        dirname = f"deepseek-forge-{session_id}-{os.getpid()}"
    else:
        dirname = f"deepseek-forge-{os.getpid()}"
    return os.path.join(tempfile.gettempdir(), dirname)


def get_artifact_path(filename: str) -> str:
    """Return a default artifact path for *filename*."""
    return os.path.join(get_artifact_dir(), filename)


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
    marker = f"## Template: `{template_name}`"
    alt_marker = f"## Template: {template_name}"

    start = content.find(marker)
    if start == -1:
        start = content.find(alt_marker)
    if start == -1:
        raise ValueError(f"Template '{template_name}' not found in {tmpl_path}")

    body_start = content.find("\n", start) + 1
    remainder = content[body_start:]
    next_section = remainder.find("\n## Template:")
    if next_section != -1:
        return remainder[:next_section].strip()
    return remainder.strip()


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
