"""File upload endpoint for chat attachments.

A file dropped into the chat is stored under ``<workspace>/uploads`` and its
text is extracted so it can be added to the model's context. The stored copy
also makes the file reachable by the agent's own file tools. Uploads are capped
by size and the extracted text is capped by length; the full file always stays
on disk for ``read_file``.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile

from claudio_core.extract import extract_text

from .deps import ApiError, require_auth

router = APIRouter()

# A single uploaded file, in bytes.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
# How much extracted text to return in the upload response (preview only; the
# chat request re-extracts with its own budget).
MAX_PREVIEW_CHARS = 2_000


def _safe_filename(name: str | None) -> str:
    """Reduce a client-supplied filename to a safe basename."""
    base = Path(name or "upload").name
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return base or "upload"


@router.post("/files/upload")
async def upload_file(request: Request, file: UploadFile = File(...)) -> dict:
    """Store one uploaded file in the workspace and report its extracted text."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    if not supervisor.config:
        raise ApiError(503, "not_ready", "Configuration not loaded")

    data = await file.read()
    if not data:
        raise ApiError(422, "validation_error", "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(
            413,
            "file_too_large",
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    ws = Path(supervisor.config.agent.workspace_dir).resolve()
    uploads = ws / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    safe = _safe_filename(file.filename)
    ts = int(time.time() * 1000)
    stored = uploads / f"{ts}-{safe}"
    while stored.exists():
        ts += 1
        stored = uploads / f"{ts}-{safe}"
    try:
        stored.write_bytes(data)
    except OSError as exc:
        raise ApiError(500, "write_failed", f"Could not store file: {exc}")

    result = extract_text(stored, MAX_PREVIEW_CHARS)
    rel = str(stored.relative_to(ws))
    return {
        "ok": True,
        "path": rel,
        "name": file.filename or safe,
        "size": len(data),
        "kind": result.kind,
        "chars": result.chars,
        "extracted": bool(result.text.strip()),
        "note": result.note,
    }
