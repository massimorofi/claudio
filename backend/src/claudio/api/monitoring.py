"""Monitoring endpoints: health, metrics, logs, runs."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import require_auth

router = APIRouter()


@router.get("/monitoring/health")
async def health(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    checks = []
    supervisor_ok = supervisor.state in ("running", "degraded")
    checks.append({"name": "supervisor", "status": "ok" if supervisor.config else "down"})
    checks.append({"name": "agent", "status": supervisor.state})
    db_ok = supervisor.db is not None
    checks.append({"name": "db", "status": "ok" if db_ok else "down"})
    llm_status = "ok"
    if supervisor.config:
        # Cheap heuristic: if recent LLM errors dominate, mark degraded.
        snap = supervisor.metrics.snapshot()
        if snap["llm_errors_total"] > 0 and snap["runs_completed_total"] == 0:
            llm_status = "degraded"
    checks.append({"name": "llm", "status": llm_status})
    overall = "ok" if all(c["status"] in ("ok", "running") for c in checks) else "degraded"
    return {
        "supervisor": "ok" if supervisor_ok else "down",
        "agent": supervisor.state,
        "llm": llm_status,
        "db": "ok" if db_ok else "down",
        "checks": checks,
        "overall": overall,
    }


@router.get("/monitoring/metrics")
async def metrics(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    return supervisor.metrics.snapshot(
        runs_active=supervisor.run_manager.active_count() if supervisor.run_manager else 0
    )


@router.get("/monitoring/logs")
async def logs(request: Request, level: str | None = None, since: str | None = None, limit: int = 200) -> dict:
    require_auth(request)
    from ..observability.logging import get_log_buffer

    entries = get_log_buffer()
    if level:
        order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        min_level = order.get(level.upper(), 0)
        entries = [e for e in entries if order.get(e.get("level", ""), 0) >= min_level]
    if since:
        entries = [e for e in entries if (e.get("ts") or "") >= since]
    return {"logs": entries[-limit:]}


@router.get("/monitoring/runs")
async def runs(request: Request, limit: int = 50) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    rows = await supervisor.db.list_runs(limit=limit)
    return {
        "runs": [
            {
                "run_id": r["id"],
                "session_id": r["session_id"],
                "state": r["state"],
                "started_at": r["started_at"],
                "turns_used": r["turns_used"],
                "cost_usd": r["cost_usd"],
            }
            for r in rows
        ]
    }
