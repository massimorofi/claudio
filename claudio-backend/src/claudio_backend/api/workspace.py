"""Workspace file management: list, delete (batch), download (single + zip).

All operations are confined to the configured workspace directory. Any path that
resolves outside it is rejected, so a crafted relative path can never reach the
rest of the filesystem.
"""
from __future__ import annotations

import io
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from .deps import require_auth, ApiError
from ..paths import resolve_workspace_dir

router = APIRouter()

# Safety cap so a pathological zip request can't blow up a single response.
_MAX_ZIP_FILES = 1000


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


async def _workspace(request: Request, session_id: str | None = None) -> Path:
    """Resolve the workspace directory the file operations act on.

    When ``session_id`` is supplied, the workspace is the one that session was
    bound to at creation (``sessions.create_session``) — the per-session model:
    opening a session shows *that* session's files, not the global default.
    Without a session (or for a legacy session with no stored workspace) we fall
    back to the configured default workspace.
    """
    supervisor = request.app.state.supervisor
    if not supervisor.config:
        raise ApiError(503, "not_ready", "Configuration not loaded")
    if session_id:
        row = await supervisor.db.get_session(session_id)
        if not row:
            raise ApiError(404, "not_found", f"No session {session_id}")
        raw = row.get("workspace_dir")
        if raw:
            ws = Path(raw)
            ws.mkdir(parents=True, exist_ok=True)
            return ws
    ws = resolve_workspace_dir(supervisor.config)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _safe_join(ws: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``ws``, refusing absolute paths and traversal."""
    if not rel or not isinstance(rel, str):
        raise ApiError(422, "validation_error", "path is required")
    candidate = (ws / rel).resolve()
    try:
        candidate.relative_to(ws)
    except ValueError:
        raise ApiError(403, "forbidden", f"Path escapes workspace: {rel}")
    if candidate == ws:
        raise ApiError(403, "forbidden", "Cannot operate on the workspace root")
    return candidate


def _build_tree(directory: Path, ws: Path) -> list[dict]:
    """Recursively build a nested tree of the workspace."""
    nodes: list[dict] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return nodes
    for entry in entries:
        try:
            st = entry.stat()
        except OSError:
            continue
        rel = str(entry.relative_to(ws))
        if entry.is_dir():
            nodes.append(
                {
                    "name": entry.name,
                    "path": rel,
                    "type": "dir",
                    "modified": _iso(st.st_mtime),
                    "children": _build_tree(entry, ws),
                }
            )
        else:
            nodes.append(
                {
                    "name": entry.name,
                    "path": rel,
                    "type": "file",
                    "size": st.st_size,
                    "modified": _iso(st.st_mtime),
                }
            )
    return nodes


@router.get("/workspace/files")
async def list_files(request: Request, session_id: str | None = None) -> dict:
    """Recursively list files in the workspace.

    Pass ``session_id`` to list the files of the workspace that session is
    bound to; omit it to use the configured default workspace.
    """
    require_auth(request)
    ws = await _workspace(request, session_id)
    files = []
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        files.append(
            {
                "path": str(p.relative_to(ws)),
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            }
        )
    return {"workspace": str(ws), "files": files}


@router.get("/workspace/tree")
async def get_tree(request: Request, session_id: str | None = None) -> dict:
    """Return the workspace as a nested tree (files and directories).

    Pass ``session_id`` to browse the workspace that session is bound to;
    omit it to use the configured default workspace.
    """
    require_auth(request)
    ws = await _workspace(request, session_id)
    tree = _build_tree(ws, ws)
    return {"workspace": str(ws), "tree": tree}


@router.post("/workspace/files/delete")
async def delete_files(request: Request, payload: dict) -> dict:
    """Delete a batch of files and/or directories by workspace-relative path.

    Directories are removed recursively. Paths are processed deepest-first so
    that selecting a folder together with some of its children never leaves a
    child reporting "not found" after its parent has already been removed.

    Pass ``session_id`` in the body to delete within the workspace that
    session is bound to; omit it to use the configured default workspace.
    """
    require_auth(request)
    ws = await _workspace(request, payload.get("session_id"))
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ApiError(422, "validation_error", "paths (non-empty list) is required")
    ordered = sorted(paths, key=lambda p: (str(p).count("/"), len(str(p))), reverse=True)
    deleted: list[str] = []
    failed: list[dict] = []
    for rel in ordered:
        try:
            target = _safe_join(ws, rel)
        except ApiError as e:
            failed.append({"path": rel, "error": e.message})
            continue
        if not target.exists():
            failed.append({"path": rel, "error": "not found"})
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            deleted.append(rel)
        except OSError as e:
            failed.append({"path": rel, "error": str(e)})
    return {"ok": True, "deleted": deleted, "failed": failed}


@router.get("/workspace/files/download")
async def download_file(request: Request, path: str, session_id: str | None = None) -> FileResponse:
    """Download a single workspace file.

    Pass ``session_id`` to download from the workspace that session is bound
    to; omit it to use the configured default workspace.
    """
    require_auth(request)
    ws = await _workspace(request, session_id)
    target = _safe_join(ws, path)
    if not target.exists() or not target.is_file():
        raise ApiError(404, "not_found", f"No such file: {path}")
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.post("/workspace/files/download")
async def download_zip(request: Request, payload: dict) -> StreamingResponse:
    """Download a batch of workspace files as a single zip archive.

    Pass ``session_id`` in the body to download from the workspace that
    session is bound to; omit it to use the configured default workspace.
    """
    require_auth(request)
    ws = await _workspace(request, payload.get("session_id"))
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ApiError(422, "validation_error", "paths (non-empty list) is required")
    if len(paths) > _MAX_ZIP_FILES:
        raise ApiError(422, "validation_error", f"Too many files (max {_MAX_ZIP_FILES})")
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in paths:
            target = _safe_join(ws, rel)
            if target.is_file():
                zf.write(target, arcname=rel)
                added += 1
            elif target.is_dir():
                for f in sorted(target.rglob("*")):
                    if f.is_file():
                        zf.write(f, arcname=str(f.relative_to(ws)))
                        added += 1
    if added == 0:
        raise ApiError(404, "not_found", "None of the requested files exist")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="workspace-files.zip"'},
    )
