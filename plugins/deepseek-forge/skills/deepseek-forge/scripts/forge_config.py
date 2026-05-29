"""Shared configuration for deepseek-forge scripts.

Reads environment variables for portability:

- ``DEEPSEEK_FORGE_HOME`` — path to the plugin/skill root directory (where
  SKILL.md lives).  Defaults to the parent of this ``scripts/`` directory.

- ``DEEPSEEK_FORGE_SESSION_ID`` — optional per-conversation identifier used
  in the default artifact directory name.  This is useful when multiple
  Codex conversations use the plugin at the same time.

- ``DEEPSEEK_FORGE_ARTIFACT_DIR`` — where runtime artifacts are written
  (patches, logs, context files, etc.).  Defaults to a temporary directory
  under ``/tmp/deepseek-forge-{session-id}-{pid}/`` when
  ``DEEPSEEK_FORGE_SESSION_ID`` is set, otherwise
  ``/tmp/deepseek-forge-{pid}/``.  Set this explicitly to use a persistent
  directory such as ``.deepseek-forge/`` in the target repo.

- ``DEEPSEEK_FORGE_LOCK_PATH`` — optional path for the per-repository lock
  directory used by write operations.

- ``DEEPSEEK_FORGE_DISABLE_REPO_LOCK`` — set to ``1`` to disable the lock.

Uses only stdlib -- no external dependencies.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import re
import shutil
import subprocess
from pathlib import Path


def _sanitize_path_component(value: str) -> str:
    """Return *value* as a conservative single path component."""
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    sanitized = sanitized.strip(".-")
    return sanitized or "session"


def get_forge_home() -> Path:
    """Return the deepseek-forge root directory.

    Uses the ``DEEPSEEK_FORGE_HOME`` environment variable if set, otherwise
    defaults to the parent of this ``scripts/`` directory.
    """
    env = os.environ.get("DEEPSEEK_FORGE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def get_session_id() -> str | None:
    """Return the optional sanitized deepseek-forge session id."""
    raw = os.environ.get("DEEPSEEK_FORGE_SESSION_ID")
    if not raw:
        return None
    return _sanitize_path_component(raw)


def get_artifact_dir() -> Path:
    """Return the artifact output directory.

    Uses the ``DEEPSEEK_FORGE_ARTIFACT_DIR`` environment variable if set,
    otherwise defaults to ``/tmp/deepseek-forge-{pid}/``.  When
    ``DEEPSEEK_FORGE_SESSION_ID`` is set, the sanitized session id is included
    in the default directory name.
    """
    env = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    session_id = get_session_id()
    if session_id:
        return Path(f"/tmp/deepseek-forge-{session_id}-{os.getpid()}")
    return Path(f"/tmp/deepseek-forge-{os.getpid()}")


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

    root = Path(repo_root or os.getcwd()).expanduser().resolve()
    git_dir = _git_dir(root)
    if git_dir is not None:
        return git_dir / "deepseek-forge.lock"
    return root / ".deepseek-forge" / "deepseek-forge.lock"


@contextmanager
def repo_lock(repo_root: str | Path | None = None, reason: str = "operation"):
    """Acquire a per-repository lock for write-sensitive operations.

    The lock is implemented as an atomically-created directory so Bash and
    Python scripts can coordinate without extra dependencies.
    """
    if os.environ.get("DEEPSEEK_FORGE_DISABLE_REPO_LOCK") == "1":
        yield None
        return

    lock_path = get_repo_lock_path(repo_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    owner_path = lock_path / "owner"

    try:
        lock_path.mkdir()
    except FileExistsError as exc:
        owner = ""
        try:
            owner = owner_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            owner = "owner unavailable"
        raise RuntimeError(
            "deepseek-forge repository lock is already held at "
            f"{lock_path}.\n{owner.strip()}\n"
            "Use a separate git worktree, wait for the other session, or set "
            "DEEPSEEK_FORGE_DISABLE_REPO_LOCK=1 if you are sure it is safe."
        ) from exc

    session_id = get_session_id() or ""
    owner_path.write_text(
        f"pid={os.getpid()}\n"
        f"session={session_id}\n"
        f"reason={reason}\n",
        encoding="utf-8",
    )

    try:
        yield lock_path
    finally:
        shutil.rmtree(lock_path, ignore_errors=True)
