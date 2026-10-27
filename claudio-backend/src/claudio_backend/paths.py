"""Path resolution and workspace validation helpers.

Relative paths in the config (``agent.default_workspace_dir``) are anchored to
the **backend base directory** — the folder that contains ``src/`` (i.e.
``claudio-backend``) — derived from this module's own location, so they resolve
identically no matter which directory the process was launched from. Without
this anchoring, a relative ``./data/workspace`` resolves against the process
cwd, so the workspace (and the ``file-history/`` backups and ADK ``.claudio``
session databases that hang off it) materialises in a different place each time
the backend is started from a different directory.

An absolute configured path is used as-is, so an operator who wants the
workspace somewhere specific can set an absolute path.
"""
from __future__ import annotations

from pathlib import Path

from claudio_core.settings import AppConfig


class WorkspaceValidationError(ValueError):
    """A candidate workspace root failed validation."""


# Absolute paths that must never be used as a workspace root: the filesystem
# root and the top-level system directories. The configured allowlist is the
# primary control; this set is a backstop for when the allowlist is empty
# (dev mode), so a misconfiguration can never expose the whole system.
_DANGEROUS_ROOTS = frozenset(
    {
        "/",
        "/home",
        "/etc",
        "/usr",
        "/var",
        "/bin",
        "/sbin",
        "/boot",
        "/root",
        "/lib",
        "/lib64",
        "/opt",
        "/sys",
        "/proc",
        "/dev",
        "/run",
    }
)


def backend_base() -> Path:
    """The backend base directory (the folder that contains ``src/``).

    Derived from this file's location (``claudio-backend/src/claudio_backend/
    paths.py``) so it does not depend on the process cwd.
    """
    return Path(__file__).resolve().parents[2]


def resolve_workspace_dir(config: AppConfig) -> Path:
    """Resolve ``agent.default_workspace_dir`` to an absolute path.

    A relative path is anchored to the backend base directory so it does not
    depend on the process cwd. An absolute path is used as-is.
    """
    p = Path(config.agent.default_workspace_dir).expanduser()
    if p.is_absolute():
        return p
    return (backend_base() / p).resolve()


def workspace_state_dir(workspace_id: str) -> Path:
    """Per-workspace runtime state directory (ADK DBs, file-history).

    Lives under the backend's own ``data/workspaces/<workspace_id>/`` tree,
    never inside a user's workspace, so Claudio's runtime state cannot pollute
    a code repo. The directory is created on demand.
    """
    d = (backend_base() / "data" / "workspaces" / workspace_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_skills_dir(config: AppConfig) -> Path:
    """Resolve ``agent.skills_dir`` to an absolute path.

    A relative ``skills_dir`` is anchored to the backend base directory (the
    folder that contains ``src/``) so it does not depend on the process cwd.
    An absolute ``skills_dir`` is used as-is. Lives here rather than in the
    engine so non-engine callers (the promotion gate, consolidation) can
    resolve the skills location without pulling ADK into their import graph.
    """
    p = Path(config.agent.skills_dir).expanduser()
    if p.is_absolute():
        return p
    return (backend_base() / p).resolve()


def validate_workspace_dir(
    raw: str,
    *,
    allowed_roots: list[str] | None = None,
    must_exist: bool = True,
) -> Path:
    """Resolve and validate a candidate workspace root.

    A workspace is exactly what the agent's file tools can read and write, so
    choosing one is a security decision and the value is validated rather than
    trusted. Returns the resolved absolute ``Path`` on success; raises
    :class:`WorkspaceValidationError` on any rule violation.

    Rules:
      1. Non-empty after ``expanduser()``.
      2. Resolve to an absolute path (relative paths anchored to the backend
         base), following symlinks deliberately — comparison is on resolved
         paths so a symlinked home does not split one directory into two.
      3. Must be an existing directory when ``must_exist``.
      4. Refuse dangerous roots (``/``, ``/home``, ``/etc`` …).
      5. Refuse the backend's own ``data/`` directory (it holds the DB).
      6. When ``allowed_roots`` is non-empty, the path must fall inside one of
         them. An empty/None allowlist disables this check (dev escape hatch).
    """
    if raw is None or not str(raw).strip():
        raise WorkspaceValidationError("workspace path is empty")

    p = Path(str(raw).strip()).expanduser()
    if not p.is_absolute():
        p = backend_base() / p
    resolved = p.resolve()

    if must_exist:
        if not resolved.exists():
            raise WorkspaceValidationError(
                f"workspace path does not exist: {resolved}"
            )
        if not resolved.is_dir():
            raise WorkspaceValidationError(
                f"workspace path is not a directory: {resolved}"
            )

    if str(resolved) in _DANGEROUS_ROOTS:
        raise WorkspaceValidationError(
            f"refusing dangerous workspace root: {resolved}"
        )

    data_dir = (backend_base() / "data").resolve()
    if resolved == data_dir:
        raise WorkspaceValidationError(
            "refusing the backend data directory as a workspace root"
        )

    if allowed_roots:
        inside = False
        for ar in allowed_roots:
            if not str(ar).strip():
                continue
            arp = Path(str(ar).strip()).expanduser().resolve()
            try:
                resolved.relative_to(arp)
                inside = True
                break
            except ValueError:
                continue
        if not inside:
            shown = ", ".join(str(r) for r in allowed_roots if str(r).strip())
            raise WorkspaceValidationError(
                f"workspace {resolved} is not inside any allowed root ({shown})"
            )

    return resolved


__all__ = [
    "backend_base",
    "resolve_skills_dir",
    "resolve_workspace_dir",
    "validate_workspace_dir",
    "workspace_state_dir",
    "WorkspaceValidationError",
]
