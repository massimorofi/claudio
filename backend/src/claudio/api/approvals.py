"""Human-in-the-loop approval endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import require_auth, ApiError

router = APIRouter()


@router.get("/approvals/pending")
async def pending_approvals(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    items = await supervisor.db.list_pending_approvals()
    return {
        "approvals": [
            {
                "approval_id": a["id"],
                "run_id": a["run_id"],
                "session_id": a["session_id"],
                "tool": a["tool_name"],
                "arguments": a["arguments"],
                "reason": a["reason"],
                "requested_at": a["requested_at"],
            }
            for a in items
        ]
    }


@router.post("/approvals/{approval_id}")
async def resolve_approval(request: Request, approval_id: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    approval = await supervisor.db.get_approval(approval_id)
    if not approval:
        raise ApiError(404, "not_found", f"No approval {approval_id}")
    if approval["state"] != "pending":
        raise ApiError(409, "already_resolved", "This approval was already resolved")

    body = await request.json()
    decision = body.get("decision")
    if decision not in ("approve", "deny"):
        raise ApiError(422, "validation_error", "decision must be 'approve' or 'deny'")
    reason = body.get("reason", "")

    await supervisor.db.resolve_approval(approval_id, decision, reason)

    # Resume the paused run from its persisted state.
    run = await supervisor.db.get_run(approval["run_id"])
    if not run or not run.get("resume_state_json"):
        raise ApiError(409, "no_resume_state", "Cannot resume: no saved run state")

    await supervisor.run_manager.resume_run(
        run_id=approval["run_id"],
        session_id=approval["session_id"],
        state_json=run["resume_state_json"],
        approval_id=approval_id,
        decision=decision,
        rejection_message=reason or None,
    )
    return {"resolved": True, "decision": decision, "run_id": approval["run_id"]}
