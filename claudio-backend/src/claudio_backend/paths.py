"""Path resolution helpers.

Relative paths in the config (``agent.workspace_dir``) are anchored to the
**backend base directory** — the folder that contains ``src/`` (i.e.
``claudio-backend``) — derived from this module's own location, so they resolve
identically no matter which directory the process was launched from. Without
this anchoring, a relative ``./data/workspace`` resolves against the process
cwd, so the workspace (and the ``file-history/`` backups and ADK ``.claudio``
session databases that hang off it) materialises in a different place each time
the backend is started from a different directory.

An absolute configured path is used as-is, so an operator who wants the
workspace somewhere specific can set an absolute ``workspace_dir``.
"""
from __future__ import annotations

from pathlib import Path

from claudio_core.settings import AppConfig


def backend_base() -> Path:
    """The backend base directory (the folder that contains ``src/``).

    Derived from this file's location (``claudio-backend/src/claudio_backend/
    paths.py``) so it does not depend on the process cwd.
    """
    return Path(__file__).resolve().parents[2]


def resolve_workspace_dir(config: AppConfig) -> Path:
    """Resolve ``agent.workspace_dir`` to an absolute path.

    A relative ``workspace_dir`` is anchored to the backend base directory so
    it does not depend on the process cwd. An absolute ``workspace_dir`` is
    used as-is.
    """
    p = Path(config.agent.workspace_dir).expanduser()
    if p.is_absolute():
        return p
    return (backend_base() / p).resolve()


__all__ = ["backend_base", "resolve_workspace_dir"]
