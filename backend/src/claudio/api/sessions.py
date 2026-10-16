"""Session endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..agent.builder import _make_model
from ..runs.manager import _estimate_items_tokens, _context_usage_payload
from .deps import require_auth, ApiError

router = APIRouter()


@router.get("/sessions")
async def list_sessions(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    rows = await supervisor.db.list_sessions()
    return {
        "sessions": [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "message_count": r["message_count"],
                "total_cost_usd": r["total_cost_usd"],
            }
            for r in rows
        ]
    }


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    row = await supervisor.db.get_session(session_id)
    if not row:
        raise ApiError(404, "not_found", f"No session {session_id}")
    out = dict(row)
    # Seed the context gauge on page load from the persisted last-turn size.
    if supervisor.config:
        last_ctx = int(row.get("last_context_tokens") or 0)
        auto_off = bool(supervisor.compactor and supervisor.compactor.is_disabled(session_id))
        out["context_usage"] = _context_usage_payload(
            last_ctx, supervisor.config, "stored", auto_disabled=auto_off
        )
    return out


@router.post("/sessions/{session_id}/compact")
async def compact_session(request: Request, session_id: str) -> dict:
    """Manually compact a session's context (Phase 4).

    Blocked while a run is active for the session: compacting mid-run races the
    run's appends to the shared session. Reuses the RunManager's ``Compactor``
    so the circuit-breaker state stays shared (a second instance would reset it).
    """
    require_auth(request)
    supervisor = request.app.state.supervisor
    row = await supervisor.db.get_session(session_id)
    if not row:
        raise ApiError(404, "not_found", f"No session {session_id}")
    if supervisor.run_manager and supervisor.run_manager.get_active_run_for_session(
        session_id
    ):
        raise ApiError(409, "busy", "A run is active for this session — finish it first")
    compactor = supervisor.compactor
    if compactor is None or supervisor.config is None:
        raise ApiError(503, "unavailable", "Compactor not initialized")
    session = supervisor.session_store.make_sdk_session(session_id)
    ok = await compactor.compact(session, _make_model(supervisor.config), session_id)
    items = await session.get_items()
    used = _estimate_items_tokens(items)
    if not ok:
        if compactor.is_disabled(session_id):
            return {"ok": False, "error": "compaction disabled (circuit breaker tripped)"}
        return {"ok": False, "error": "nothing to compact"}
    await supervisor.db.update_session(session_id, last_context_tokens=used)
    return {
        "ok": True,
        "used_tokens": used,
        "free_tokens": max(0, compactor.context_window - compactor.reserve - used),
        "context_usage": _context_usage_payload(
            used, supervisor.config, "estimate", auto_disabled=False
        ),
    }


@router.get("/sessions/{session_id}/messages")
async def get_messages(request: Request, session_id: str, limit: int = 200) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    row = await supervisor.db.get_session(session_id)
    if not row:
        raise ApiError(404, "not_found", f"No session {session_id}")
    entries = supervisor.session_store.read(session_id)
    repaired = supervisor.session_store.repair_orphans(entries)
    return {"messages": repaired[-limit:]}


@router.get("/sessions/{session_id}/active_run")
async def get_active_run(request: Request, session_id: str) -> dict:
    """Return the in-flight run for a session, if one is still going.

    Lets the UI reattach to a run that was started before a page reload.
    """
    require_auth(request)
    supervisor = request.app.state.supervisor
    record = supervisor.run_manager.get_active_run_for_session(session_id)
    if record is None:
        return {"active": False}
    return {
        "active": True,
        "run_id": record.run_id,
        "state": record.state,
        "last_seq": record.seq,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    await supervisor.db.delete_session(session_id)
    supervisor.session_store.delete(session_id)
    return {"deleted": True}


@router.post("/sessions/{session_id}/clear")
async def clear_session(request: Request, session_id: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    row = await supervisor.db.get_session(session_id)
    if not row:
        raise ApiError(404, "not_found", f"No session {session_id}")
    supervisor.session_store.clear(session_id)
    await supervisor.db.update_session(
        session_id, message_count=0, turns_since_todo=0
    )
    return {"cleared": True}
