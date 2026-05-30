"""Shared configuration for deepseek-forge scripts.

Reads environment variables for portability:

- ``DEEPSEEK_FORGE_HOME`` — path to the plugin/skill root directory (where
  SKILL.md lives).  Defaults to the parent of this ``scripts/`` directory.

- ``DEEPSEEK_FORGE_SESSION_ID`` — optional per-conversation identifier used
  in the default artifact directory name.  This is useful when multiple
  Codex conversations use the plugin at the same time.

- ``DEEPSEEK_FORGE_ARTIFACT_DIR`` — where runtime artifacts are written
  (patches, logs, context files, etc.).  Defaults to a temporary directory
  under ``/tmp/deepseek-forge/{repo_hash}/{thread_id}/{run_id}/``.
  Set this explicitly to use a custom base path; thread/run subdirectories
  are still auto-appended for isolation.

- ``DEEPSEEK_FORGE_LOCK_PATH`` — optional path for the per-repository lock
  directory used by write operations.

- ``DEEPSEEK_FORGE_DISABLE_REPO_LOCK`` — set to ``1`` to disable the lock.

- ``DEEPSEEK_FORGE_RUN_ID`` — optional override for the run-id component of
  the artifact path.  When not set, a timestamp-based id is generated.

- ``DEEPSEEK_FORGE_MAX_LOOPS`` — maximum forward-development loop iterations.
  Default ``5``.

- ``DEEPSEEK_FORGE_MAX_PARALLEL_AGENTS`` — max parallel DeepSeek sub-agents
  per loop iteration.  Default ``3``.

- ``DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS`` — when ``"true"``, permits writing
  loop artifacts into ``.deepseek-forge/`` inside the repository.  Default
  ``"false"``.

- ``CODEX_THREAD_ID`` — set by Codex to isolate concurrent conversations.
  Used as a subdirectory component in the artifact path.

Uses only stdlib -- no external dependencies.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path


def _sanitize_path_component(value: str) -> str:
    """Return *value* as a conservative single path component."""
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    sanitized = sanitized.strip(".-")
    return sanitized or "session"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent


def _find_git_root() -> Path | None:
    """Walk upward from cwd to find the nearest git repository root."""
    d = Path.cwd().resolve()
    while True:
        if (d / ".git").exists():
            return d
        parent = d.parent
        if parent == d:
            return None
        d = parent


def _repo_hash() -> str:
    """Return a short hash of the git repo root path for artifact isolation."""
    root = _find_git_root()
    if root is None:
        root = Path.cwd().resolve()
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    return digest


def _thread_id() -> str:
    """Return the Codex thread id, or a fallback placeholder."""
    tid = os.environ.get("CODEX_THREAD_ID", "").strip()
    if tid:
        return tid
    return f"unknown-{os.getpid()}"


def _run_id() -> str:
    """Return the run id from env or generate a timestamp-based one."""
    rid = os.environ.get("DEEPSEEK_FORGE_RUN_ID", "").strip()
    if rid:
        return rid
    return f"run-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def get_forge_home() -> Path:
    """Return the deepseek-forge root directory.

    Uses the ``DEEPSEEK_FORGE_HOME`` environment variable if set, otherwise
    defaults to the parent of this ``scripts/`` directory.
    """
    env = os.environ.get("DEEPSEEK_FORGE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return _SCRIPT_DIR.parent


def get_session_id() -> str | None:
    """Return the optional sanitized deepseek-forge session id."""
    raw = os.environ.get("DEEPSEEK_FORGE_SESSION_ID")
    if not raw:
        return None
    return _sanitize_path_component(raw)


def get_artifact_dir() -> Path:
    """Return the isolated artifact output directory.

    Resolution logic:

    1. If ``DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS`` is ``"true"``, the base
       directory is ``{git_root}/.deepseek-forge/``.
    2. Else, if ``DEEPSEEK_FORGE_ARTIFACT_DIR`` is set, use it as the base.
    3. Otherwise, default to ``/tmp/deepseek-forge/`` with isolation subdirs.

    In all cases, ``{repo_hash}/{thread_id}/{run_id}/`` is appended as
    subdirectories for concurrent-session isolation.
    """
    repo_local = os.environ.get("DEEPSEEK_FORGE_REPO_LOCAL_ARTIFACTS", "false")
    if repo_local.lower() in ("true", "1"):
        root = _find_git_root()
        if root is None:
            root = Path.cwd().resolve()
        base = root / ".deepseek-forge"
    else:
        env = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
        if env:
            base = Path(env).expanduser().resolve()
        else:
            base = Path("/tmp/deepseek-forge")

    return base / _repo_hash() / _thread_id() / _run_id()


def ensure_artifact_dir() -> Path:
    """Return the artifact directory, creating it if it does not exist."""
    d = get_artifact_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _git_dir(repo_root: Path) -> Path | None:
    """Return the resolved .git directory for *repo_root*, if available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    raw = result.stdout.strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def get_repo_lock_path(repo_root: str | Path | None = None) -> Path:
    """Return the lock directory path for repository-mutating operations.

    The default is ``.git/deepseek-forge.lock`` for Git repositories, which
    avoids polluting the worktree.  Outside Git, it falls back to
    ``.deepseek-forge/deepseek-forge.lock`` under the current directory.
    """
    env = os.environ.get("DEEPSEEK_FORGE_LOCK_PATH")
    if env:
        return Path(env).expanduser().resolve()

    root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
    git_dir = _git_dir(root)

    if git_dir is not None:
        # We resolved .git to a directory (e.g. /path/.git or
        # /path/.git/modules/sub).  Put the lock inside it.
        return git_dir / "deepseek-forge.lock"

    # Not a Git repo — fall back to the worktree.
    return root / ".deepseek-forge" / "deepseek-forge.lock"


def is_repo_lock_disabled() -> bool:
    """Return ``True`` when the repository lock is explicitly disabled."""
    return os.environ.get("DEEPSEEK_FORGE_DISABLE_REPO_LOCK", "").strip() == "1"


@contextmanager
def acquire_repo_lock(repo_root: str | Path | None = None):
    """Acquire a directory-based repository lock and release it on exit.

    The lock is advisory: only operations that call this function will be
    serialised.  The lock is skipped when ``DEEPSEEK_FORGE_DISABLE_REPO_LOCK``
    is ``"1"``.

    On failure a :class:`RuntimeError` is raised after a small number of
    retries.  The caller should treat the lock failure as a hard error.
    """
    if is_repo_lock_disabled():
        yield
        return

    lock_path = get_repo_lock_path(repo_root)
    attempts = 0
    max_attempts = 10
    while True:
        try:
            lock_path.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            attempts += 1
            if attempts >= max_attempts:
                raise RuntimeError(
                    f"Could not acquire repository lock at {lock_path} "
                    f"after {max_attempts} attempts."
                )
            time.sleep(0.1 * attempts)

    try:
        yield
    finally:
        try:
            shutil.rmtree(lock_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Loop / parallelism config
# ---------------------------------------------------------------------------


def get_max_loops() -> int:
    """Return the maximum number of forward-development loop iterations."""
    val = os.environ.get("DEEPSEEK_FORGE_MAX_LOOPS", "5")
    try:
        n = int(val)
        return max(1, n)
    except ValueError:
        return 5


def get_max_parallel_agents() -> int:
    """Return the maximum number of parallel DeepSeek sub-agents per loop."""
    val = os.environ.get("DEEPSEEK_FORGE_MAX_PARALLEL_AGENTS", "3")
    try:
        n = int(val)
        return max(1, min(n, 8))
    except ValueError:
        return 3


# ---------------------------------------------------------------------------
# repo_lock — a callable context-manager class used by apply_patch_safe.py
# ---------------------------------------------------------------------------


class repo_lock:
    """Context manager for per-repository serialisation.

    Usage::

        with repo_lock("/path/to/repo", reason="apply-patch") as held:
            ...

    *held* is the lock directory :class:`Path` when locked, or ``None`` when
    the lock is disabled via ``DEEPSEEK_FORGE_DISABLE_REPO_LOCK``.

    Each ``repo_lock(...)`` call creates a fresh instance, so there is no
    shared mutable state between concurrent lock acquisitions.
    """

    def __init__(self, repo_root: str | Path | None = None, *,
                 reason: str = "") -> None:
        self._repo_root = repo_root
        self._reason = reason
        self._lock_path: Path | None = None

    def __enter__(self) -> Path | None:
        if is_repo_lock_disabled():
            return None

        self._lock_path = get_repo_lock_path(self._repo_root)

        try:
            self._lock_path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise RuntimeError(
                f"Lock already held at {self._lock_path}"
            )

        # Write owner metadata for diagnostics.
        owner_parts: list[str] = []
        sid = os.environ.get("DEEPSEEK_FORGE_SESSION_ID", "").strip()
        if sid:
            owner_parts.append(f"session={_sanitize_path_component(sid)}")
        else:
            owner_parts.append(f"pid={os.getpid()}")
        if self._reason:
            owner_parts.append(f"reason={self._reason}")
        (self._lock_path / "owner").write_text(
            "\n".join(owner_parts), encoding="utf-8"
        )

        return self._lock_path

    def __exit__(self, *args) -> None:
        if self._lock_path is not None and self._lock_path.exists():
            try:
                shutil.rmtree(self._lock_path)
            except OSError:
                pass
        return None
