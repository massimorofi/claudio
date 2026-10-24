"""Read-side API for the self-improvement episodic store.

Exposes the captured learnings for the Memory view: list/filter, full-text
search, the category breakdown that drives the filter dropdown, and summary
counts. The write side (consolidation, draft approval) lives in
:mod:`claudio_backend.autonomous.consolidation` and the draft-approval routes
in this module's lower half.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from claudio_backend.autonomous.promotion import promote_draft, reject_draft

from .deps import require_auth, ApiError

router = APIRouter()


@router.get("/learning")
async def list_learnings(
    request: Request,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = 100,
) -> dict:
    """List learnings, optionally filtered by status/category or full-text ``q``.

    When ``q`` is present the query hits the FTS index and results are ranked
    by relevance; otherwise they come back newest-first.
    """
    require_auth(request)
    supervisor = request.app.state.supervisor
    limit = max(1, min(limit, 500))
    if q and q.strip():
        rows = await supervisor.db.search_learnings(
            q, status=status, category=category, limit=limit
        )
    else:
        rows = await supervisor.db.list_learnings(
            status=status, category=category, limit=limit
        )
    return {"learnings": rows}


@router.get("/learning/categories")
async def learning_categories(request: Request) -> dict:
    """Distinct categories with their counts, most common first."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    categories = await supervisor.db.list_learning_categories()
    return {"categories": categories}


@router.get("/learning/stats")
async def learning_stats(request: Request) -> dict:
    """Headline counts for the Memory view header."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    total = await supervisor.db.count_learnings()
    raw = await supervisor.db.count_learnings(status="raw")
    processed = await supervisor.db.count_learnings(status="processed")
    promoted = await supervisor.db.count_learnings(status="promoted")
    return {
        "total": total,
        "raw": raw,
        "processed": processed,
        "promoted": promoted,
    }


@router.get("/learning/consolidations")
async def list_consolidations(request: Request, limit: int = 20) -> dict:
    """History of consolidation passes, most recent first."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    limit = max(1, min(limit, 200))
    rows = await supervisor.db.list_consolidations(limit=limit)
    return {"consolidations": rows}


# ---- skill drafts (promotion gate) -------------------------------------


class RejectBody(BaseModel):
    note: str = ""


@router.get("/skill-drafts")
async def list_drafts(request: Request, status: str | None = None) -> dict:
    """List skill drafts, optionally filtered by status (pending/approved/rejected)."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    drafts = await supervisor.db.list_skill_drafts(status=status)
    return {"drafts": drafts}


@router.get("/skill-drafts/{draft_id}")
async def get_draft(request: Request, draft_id: int) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    draft = await supervisor.db.get_skill_draft(draft_id)
    if draft is None:
        raise ApiError(404, "not_found", f"No skill draft {draft_id}")
    return {"draft": draft}


@router.post("/skill-drafts/{draft_id}/approve")
async def approve_draft(request: Request, draft_id: int) -> dict:
    """Approve a draft: validate it and write it into the live skills directory.

    The skill goes live on the next run (the engine re-scans per request). A
    draft that fails validation is rejected automatically and the reason is
    returned.
    """
    require_auth(request)
    supervisor = request.app.state.supervisor
    try:
        result = await promote_draft(
            supervisor.db, supervisor.config, draft_id, actor="operator"
        )
    except ValueError as exc:
        raise ApiError(422, "validation_failed", str(exc))
    return {"result": result}


@router.post("/skill-drafts/{draft_id}/reject")
async def reject_draft_endpoint(request: Request, draft_id: int, body: RejectBody) -> dict:
    """Reject a pending draft without writing anything."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    try:
        result = await reject_draft(
            supervisor.db, draft_id, note=body.note, actor="operator"
        )
    except ValueError as exc:
        raise ApiError(404, "not_found", str(exc))
    return {"result": result}
