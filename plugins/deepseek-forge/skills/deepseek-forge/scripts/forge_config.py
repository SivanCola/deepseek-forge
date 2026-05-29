"""Shared configuration for deepseek-forge scripts.

Reads environment variables for portability:

- ``DEEPSEEK_FORGE_HOME`` — path to the plugin/skill root directory (where
  SKILL.md lives).  Defaults to the parent of this ``scripts/`` directory.

- ``DEEPSEEK_FORGE_ARTIFACT_DIR`` — where runtime artifacts are written
  (patches, logs, context files, etc.).  Defaults to a temporary directory
  under ``/tmp/deepseek-forge-{pid}/``.  Set this explicitly to use a
  persistent directory such as ``.deepseek-forge/`` in the target repo.

Uses only stdlib -- no external dependencies.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_forge_home() -> Path:
    """Return the deepseek-forge root directory.

    Uses the ``DEEPSEEK_FORGE_HOME`` environment variable if set, otherwise
    defaults to the parent of this ``scripts/`` directory.
    """
    env = os.environ.get("DEEPSEEK_FORGE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def get_artifact_dir() -> Path:
    """Return the artifact output directory.

    Uses the ``DEEPSEEK_FORGE_ARTIFACT_DIR`` environment variable if set,
    otherwise defaults to ``/tmp/deepseek-forge-{pid}/``.
    """
    env = os.environ.get("DEEPSEEK_FORGE_ARTIFACT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(f"/tmp/deepseek-forge-{os.getpid()}")


def ensure_artifact_dir() -> Path:
    """Return the artifact directory, creating it if it does not exist."""
    d = get_artifact_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d
