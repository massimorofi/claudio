"""Workspace registry endpoints: list, add, remove, and browse.

A workspace is a folder the user has made available for sessions to bind to.
The registry lives in ``claudio_workspaces`` (see ``claudio_core.db``). Adding
a workspace validates the path against the configured allowlist first; the
folder must exist. Removing one only drops the registry row — it never touches
anything on disk.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from .deps import require_auth, ApiError
from ..paths import validate_workspace_dir, WorkspaceValidationError

router = APIRouter()


@router.get("/workspaces")
async def list_workspaces(request: Request) -> dict:
    """List registered workspaces, most-recently-used first."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    items = await supervisor.db.list_workspaces()
    return {"workspaces": items}


@router.post("/workspaces")
async def add_workspace(request: Request) -> dict:
    """Register a new workspace from ``{ path, label }`` (validated)."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    body = await request.json()
    raw = body.get("path", "")
    label = body.get("label", "")
    allowed = supervisor.config.agent.workspace_allowed_roots
    try:
        ws = validate_workspace_dir(raw, allowed_roots=allowed, must_exist=True)
    except WorkspaceValidationError as e:
        raise ApiError(422, "validation_error", f"Invalid workspace: {e}")
    rec = await supervisor.db.register_workspace(str(ws), label=label)
    return {"workspace": rec}


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(request: Request, workspace_id: str) -> dict:
    """Remove a workspace from the registry. Does not touch the folder on disk."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    await supervisor.db.delete_workspace(workspace_id)
    return {"ok": True}


@router.get("/workspaces/suggest")
async def suggest_dirs(request: Request, path: str | None = None) -> dict:
    """Browse directories for the "add workspace" dialog.

    Returns the immediate subdirectories of ``path`` (or of each allowed root
    when no path is given), filtered to those inside the configured allowlist.
    Hidden directories are omitted.
    """
    require_auth(request)
    supervisor = request.app.state.supervisor
    allowed = supervisor.config.agent.workspace_allowed_roots

    if path:
        try:
            base = validate_workspace_dir(path, allowed_roots=allowed, must_exist=True)
        except WorkspaceValidationError as e:
            raise ApiError(422, "validation_error", f"Invalid path: {e}")
        bases = [base]
    elif allowed:
        bases = [Path(r).expanduser().resolve() for r in allowed if str(r).strip()]
    else:
        bases = [Path.home()]

    dirs: list[dict] = []
    for base in bases:
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in entries:
            if not child.is_dir() or child.name.startswith("."):
                continue
            resolved = child.resolve()
            if allowed:
                ok = False
                for ar in allowed:
                    try:
                        resolved.relative_to(Path(str(ar).strip()).expanduser().resolve())
                        ok = True
                        break
                    except ValueError:
                        continue
                if not ok:
                    continue
            dirs.append({"path": str(resolved), "name": child.name})
    return {"dirs": dirs}
