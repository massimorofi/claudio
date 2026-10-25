"""Chat / command endpoints — the agentic surface (SSE streaming)."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from claudio_core.file_context import compose_message_with_files

from .deps import require_auth, ApiError
from ..paths import validate_workspace_dir, WorkspaceValidationError

router = APIRouter()

HEARTBEAT_SECONDS = 15


async def _bind_workspace(
    supervisor, body: dict, *, session_id: str, is_new: bool
) -> tuple[Path, str]:
    """Resolve the ``(workspace path, workspace_id)`` this turn operates on.

    New session: use the workspace the client asked for (or the configured
    default), validate it, register it, and bind it to the new session row.

    Resume: the session's stored workspace **always wins**. If the stored path
    no longer exists, fail loudly with ``409 workspace_unavailable`` — never
    silently fall back to the default, which is exactly the bug this change
    removes: a transcript whose file paths refer to one tree while the tools
    operate on another.
    """
    allowed = supervisor.config.agent.workspace_allowed_roots

    if is_new:
        raw = body.get("workspace_dir") or supervisor.config.agent.default_workspace_dir
        try:
            ws = validate_workspace_dir(raw, allowed_roots=allowed)
        except WorkspaceValidationError as e:
            raise ApiError(422, "validation_error", f"Invalid workspace: {e}")
        ws.mkdir(parents=True, exist_ok=True)
        rec = await supervisor.db.register_workspace(str(ws))
        return ws, rec["id"]

    existing = await supervisor.db.get_session(session_id)
    stored = (existing or {}).get("workspace_dir") or ""
    if not stored:
        # Legacy session with no workspace bound: adopt the configured default
        # now and backfill the row so the binding is durable from here on.
        raw = supervisor.config.agent.default_workspace_dir
        try:
            ws = validate_workspace_dir(raw, allowed_roots=allowed)
        except WorkspaceValidationError as e:
            raise ApiError(422, "validation_error", f"Invalid workspace: {e}")
        ws.mkdir(parents=True, exist_ok=True)
        rec = await supervisor.db.register_workspace(str(ws))
        await supervisor.db.update_session(session_id, workspace_dir=str(ws))
        return ws, rec["id"]

    try:
        ws = validate_workspace_dir(stored, allowed_roots=allowed, must_exist=True)
    except WorkspaceValidationError as e:
        raise ApiError(
            409,
            "workspace_unavailable",
            f"Session '{session_id}' was created in {stored}, which no longer "
            "exists. Start a new session, or restore the folder.",
        )
    rec = await supervisor.db.register_workspace(stored)
    return ws, rec["id"]


def _resolve_files(message: str, files: Any, workspace_dir: str) -> tuple[str, list[str]]:
    """Build the model-facing message with any attached file contents.

    Returns ``(model_message, display_names)``. With no files the message is
    returned unchanged. The full files stay on disk in the workspace; only the
    extracted text is appended to the message.
    """
    if not files or not isinstance(files, list):
        return message, []
    ws = Path(workspace_dir).resolve()
    model_message, summaries = compose_message_with_files(message, files, ws)
    names = [s.get("path", "").split("/")[-1] for s in summaries if s.get("ok")]
    return model_message, names


def _format_sse(event: dict, event_id: int | None = None) -> str:
    """Format one SSE frame."""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event.get('event', 'message')}")
    data = json.dumps(event.get("data", {}), default=str)
    lines.append(f"data: {data}")
    return "\n".join(lines) + "\n\n"


@router.post("/chat/stream")
async def chat_stream(request: Request) -> StreamingResponse:
    """Send a command and stream the agent's answer as Server-Sent Events."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    if supervisor.state != "running":
        raise ApiError(
            409,
            "agent_not_running",
            "Agent runtime is stopped. Start it from the Configuration tab.",
            details={"agent_state": supervisor.state},
        )

    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        raise ApiError(422, "validation_error", "message is required")
    session_id = body.get("session_id")
    approval_mode = body.get("approval_mode")
    files = body.get("files") or []

    # Resolve the workspace this turn operates on. A new session takes the
    # workspace the client asked for (or the configured default); a resumed
    # session keeps the workspace it was created with, whatever the current
    # default is.
    if session_id:
        existing = await supervisor.db.get_session(session_id)
        is_new = existing is None
    else:
        is_new = True
    workspace, workspace_id = await _bind_workspace(
        supervisor, body, session_id=session_id or "", is_new=is_new
    )

    # Build the model-facing message with any attached file contents, into the
    # workspace this turn operates on.
    model_message, file_names = _resolve_files(message, files, str(workspace))

    if is_new:
        session_id = session_id or str(uuid.uuid4())
        await supervisor.db.create_session(
            session_id,
            title=message[:60],
            workspace_dir=str(workspace),
            workspace_origin=body.get("workspace_origin", "webui"),
        )

    # Mirror the user message to the transcript so a reload can show the full
    # conversation even while this run is still in flight. The transcript keeps
    # the original text plus a short attachment note; the full extracted file
    # content goes only to the model.
    display = message
    if file_names:
        display = f"{message}\n\n📎 " + ", ".join(file_names)
    supervisor.session_store.append(
        session_id,
        {"type": "user", "content": display, "files": file_names},
    )

    run_id = str(uuid.uuid4())
    await supervisor.run_manager.start_run(
        run_id=run_id,
        session_id=session_id,
        message=model_message,
        approval_mode=approval_mode,
        workspace=workspace,
        workspace_id=workspace_id,
    )

    async def event_stream():
        opened = await supervisor.run_manager.open_stream(run_id, after_seq=0)
        if opened is None:
            return
        queue, replay = opened
        seq = replay[-1][0] if replay else 0
        try:
            for evt_seq, evt in replay:
                yield _format_sse(evt, event_id=evt_seq)
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield _format_sse({"event": "heartbeat", "data": {}}, event_id=seq)
                    continue
                if evt is None:  # terminal sentinel
                    break
                seq += 1
                yield _format_sse(evt, event_id=seq)
        finally:
            await supervisor.run_manager.close_stream(run_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat")
async def chat(request: Request) -> dict:
    """Non-streaming variant: block until the run completes."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    if supervisor.state != "running":
        raise ApiError(
            409,
            "agent_not_running",
            "Agent runtime is stopped.",
            details={"agent_state": supervisor.state},
        )
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        raise ApiError(422, "validation_error", "message is required")
    session_id = body.get("session_id")
    approval_mode = body.get("approval_mode")
    files = body.get("files") or []

    # Resolve the workspace this turn operates on (new session binds, resumed
    # session keeps its own — see ``_bind_workspace``).
    if session_id:
        existing = await supervisor.db.get_session(session_id)
        is_new = existing is None
    else:
        is_new = True
    workspace, workspace_id = await _bind_workspace(
        supervisor, body, session_id=session_id or "", is_new=is_new
    )

    model_message, file_names = _resolve_files(message, files, str(workspace))

    if is_new:
        session_id = session_id or str(uuid.uuid4())
        await supervisor.db.create_session(
            session_id,
            title=message[:60],
            workspace_dir=str(workspace),
            workspace_origin=body.get("workspace_origin", "webui"),
        )

    display = message
    if file_names:
        display = f"{message}\n\n📎 " + ", ".join(file_names)
    supervisor.session_store.append(
        session_id,
        {"type": "user", "content": display, "files": file_names},
    )

    run_id = str(uuid.uuid4())
    record = await supervisor.run_manager.start_run(
        run_id=run_id,
        session_id=session_id,
        message=model_message,
        approval_mode=approval_mode,
        workspace=workspace,
        workspace_id=workspace_id,
    )
    # Wait for the run to reach a terminal state.
    try:
        await asyncio.wait_for(
            record.task, timeout=supervisor.config.llm.timeout_seconds * 2
        )
    except asyncio.TimeoutError:
        raise ApiError(504, "timeout", "Run did not finish in time")

    if record.state == "completed":
        return {
            "run_id": run_id,
            "session_id": session_id,
            "final_output": record.final_output,
            "usage": {
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "cost_usd": record.cost_usd,
            },
        }
    if record.state == "awaiting_approval":
        raise ApiError(
            409,
            "approval_required",
            "The run paused for approval; use the streaming endpoint.",
            details={"run_id": run_id},
        )
    raise ApiError(
        500,
        record.error_code or "internal_error",
        record.error_message or "Run failed",
    )


@router.post("/chat/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    ok = await supervisor.run_manager.cancel_run(run_id)
    if not ok:
        raise ApiError(404, "not_found", f"No run {run_id}")
    return {"cancelled": True}


@router.get("/chat/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    record = supervisor.run_manager.get_record(run_id)
    if record is None:
        db_run = await supervisor.db.get_run(run_id)
        if not db_run:
            raise ApiError(404, "not_found", f"No run {run_id}")
        return db_run
    return {
        "run_id": record.run_id,
        "session_id": record.session_id,
        "state": record.state,
        "turns_used": record.turns_used,
        "cost_usd": record.cost_usd,
        "events": [evt for _seq, evt in record.events],
    }


@router.get("/chat/runs/{run_id}/stream")
async def resume_stream(
    request: Request, run_id: str, last_event_id: int = 0
) -> StreamingResponse:
    """Reconnect to an in-flight run's SSE stream from last_event_id."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    record = supervisor.run_manager.get_record(run_id)
    if record is None:
        raise ApiError(404, "not_found", f"No run {run_id}")

    async def event_stream():
        opened = await supervisor.run_manager.open_stream(
            run_id, after_seq=last_event_id
        )
        if opened is None:
            return
        queue, replay = opened
        seq = last_event_id
        try:
            for evt_seq, evt in replay:
                seq = evt_seq
                yield _format_sse(evt, event_id=seq)
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield _format_sse({"event": "heartbeat", "data": {}}, event_id=seq)
                    continue
                if evt is None:
                    break
                seq += 1
                yield _format_sse(evt, event_id=seq)
        finally:
            await supervisor.run_manager.close_stream(run_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
