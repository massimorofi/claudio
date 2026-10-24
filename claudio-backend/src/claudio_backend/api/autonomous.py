"""Autonomous / daemon cockpit endpoints (Phase 1: Watch).

Read-mostly surface for the operator: system health, the check history, the
open issues, and the actions to acknowledge/resolve them or trigger a check
on demand. The daemon's *policy* (the ``autonomous`` config block) is edited
through the normal ``/config`` endpoints, not here — this router reads state
and offers the watch-phase actions.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from claudio_core.db import utcnow

from ..autonomous import fixer
from .deps import require_auth, ApiError

router = APIRouter()


@router.get("/autonomous/status")
async def status(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    config = supervisor.config
    a = config.autonomous
    latest = await supervisor.db.get_latest_health_check()
    open_count = await supervisor.db.count_open_issues()
    scheduler = getattr(supervisor, "health_scheduler", None)
    task_scheduler = getattr(supervisor, "task_scheduler", None)
    task_count = len(await supervisor.db.list_tasks())
    next_task_fire = None
    if task_scheduler and task_scheduler._next_fire:
        next_task_fire = min(task_scheduler._next_fire.values()).isoformat()
    maintenance_next = None
    if task_scheduler:
        nf = task_scheduler._next_fire.get("builtin:maintenance")
        if nf:
            maintenance_next = nf.isoformat()
    return {
        "enabled": a.enabled,
        "report_only": a.report_only,
        "health_check_enabled": a.health_check.enabled,
        "interval_minutes": a.health_check.interval_minutes,
        "notification_enabled": a.notification.enabled,
        "notification_channels": _enabled_channels(config),
        "last_check": latest,
        "open_issues_count": open_count,
        "scheduler_running": bool(scheduler and scheduler._task and not scheduler._task.done()),
        "last_run_at": scheduler.last_run_at if scheduler else None,
        "next_run_at": scheduler.next_run_at if scheduler else None,
        "maintenance_enabled": a.maintenance.enabled,
        "maintenance_schedule": a.maintenance.schedule,
        "maintenance_next_run": maintenance_next,
        "recovery_enabled": a.recovery.enabled,
        "task_count": task_count,
        "task_scheduler_running": bool(
            task_scheduler and task_scheduler._task and not task_scheduler._task.done()
        ),
        "next_task_fire_at": next_task_fire,
    }


@router.get("/autonomous/health")
async def health_latest(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    latest = await supervisor.db.get_latest_health_check()
    return {"check": latest}


@router.get("/autonomous/health/history")
async def health_history(request: Request, limit: int = 50) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    limit = max(1, min(limit, 500))
    rows = await supervisor.db.list_health_checks(limit=limit)
    return {"checks": rows}


@router.post("/autonomous/health/run")
async def health_run(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    service = supervisor.health_service
    result = await service.run_once(source="manual")
    return result


@router.post("/autonomous/maintenance/run")
async def maintenance_run(request: Request) -> dict:
    """Run one built-in maintenance pass now (the scheduler runs it on its cron)."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    report = await supervisor.task_runner.run_builtin("maintenance", trigger="manual")
    return {"report": report.to_dict()}


@router.post("/autonomous/recovery/run")
async def recovery_run(request: Request) -> dict:
    """Run one built-in disaster-recovery pass now."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    report = await supervisor.task_runner.run_builtin("recovery", trigger="manual")
    return {"report": report.to_dict()}


@router.post("/autonomous/consolidation/run")
async def consolidation_run(request: Request) -> dict:
    """Run one self-improvement consolidation pass now (the scheduler runs it on its cron)."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    report = await supervisor.task_runner.run_builtin("consolidation", trigger="manual")
    return {"report": report.to_dict()}


@router.get("/autonomous/issues")
async def list_issues(request: Request, status: str | None = None) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    issues = await supervisor.db.list_issues(status=status)
    return {"issues": issues}


@router.post("/autonomous/issues/{issue_id}/acknowledge")
async def acknowledge_issue(request: Request, issue_id: int) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    issue = await supervisor.db.get_issue(issue_id)
    if not issue:
        raise ApiError(404, "not_found", f"No issue {issue_id}")
    await supervisor.db.update_issue_status(issue_id, "acknowledged")
    updated = await supervisor.db.get_issue(issue_id)
    return {"issue": updated}


@router.post("/autonomous/issues/{issue_id}/resolve")
async def resolve_issue(request: Request, issue_id: int, payload: dict | None = None) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    issue = await supervisor.db.get_issue(issue_id)
    if not issue:
        raise ApiError(404, "not_found", f"No issue {issue_id}")
    resolution = (payload or {}).get("resolution")
    await supervisor.db.update_issue_status(issue_id, "resolved", resolution=resolution)
    updated = await supervisor.db.get_issue(issue_id)
    return {"issue": updated}


@router.post("/autonomous/notify/test")
async def notify_test(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    result = await supervisor.health_service.notifier.test()
    return result


# ---- Fix suggestions (agent proposes, operator approves each command) ----


@router.post("/autonomous/issues/{issue_id}/propose-fix")
async def propose_fix(request: Request, issue_id: int) -> dict:
    """Ask the agent to propose a fix command for this issue.

    Commands already suggested for this issue are fed back to the agent so it
    proposes something new rather than repeating a fix that was already tried.
    """
    require_auth(request)
    supervisor = request.app.state.supervisor
    issue = await supervisor.db.get_issue(issue_id)
    if not issue:
        raise ApiError(404, "not_found", f"No issue {issue_id}")
    prior = await supervisor.db.list_suggestions_for_issue(issue_id)
    tried = [
        (s["command"], f"already proposed (status: {s.get('status')})")
        for s in prior
        if s.get("command")
    ]
    try:
        proposal = await fixer.propose_fix(issue, supervisor.config, tried=tried)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, "proposal_failed", f"Could not generate a fix: {exc}")
    sid = await supervisor.db.add_suggestion(
        issue_id=issue_id,
        command=proposal.command,
        explanation=proposal.explanation,
        risk=proposal.risk,
        reversible=proposal.reversible,
    )
    await supervisor.db.add_audit(
        action="suggestion_proposed",
        command=proposal.command,
        detail=f"for issue {issue_id}",
    )
    suggestion = await supervisor.db.get_suggestion(sid)
    return {"suggestion": suggestion}


@router.get("/autonomous/issues/{issue_id}/suggestions")
async def list_suggestions(request: Request, issue_id: int) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    rows = await supervisor.db.list_suggestions_for_issue(issue_id)
    return {"suggestions": rows}


@router.post("/autonomous/suggestions/{suggestion_id}/accept")
async def accept_suggestion(
    request: Request, suggestion_id: int, payload: dict | None = None
) -> dict:
    """Run the approved command, audit it, then re-check health.

    The operator may include ``{"password": "..."}`` in the body to supply the
    sudo password for privileged commands. The password is passed straight to
    the command's stdin and is deliberately NOT written to the audit log, the
    suggestion record, or any captured output.

    If the re-check comes back OK the originating issue is auto-resolved.
    """
    require_auth(request)
    supervisor = request.app.state.supervisor
    suggestion = await supervisor.db.get_suggestion(suggestion_id)
    if not suggestion:
        raise ApiError(404, "not_found", f"No suggestion {suggestion_id}")
    if suggestion["status"] != "pending":
        raise ApiError(409, "already_handled", f"Suggestion is {suggestion['status']}")

    command = suggestion["command"]
    issue_id = suggestion["issue_id"]
    password = (payload or {}).get("password") or None

    result = await fixer.execute_fix(command, password=password)
    await supervisor.db.add_audit(
        action="fix_executed",
        command=command,
        exit_code=result.get("exit_code"),
        detail=f"suggestion {suggestion_id} for issue {issue_id}",
    )
    await supervisor.db.update_suggestion(
        suggestion_id,
        status="executed" if result["ok"] else "failed",
        executed_at=utcnow(),
        exit_code=result.get("exit_code"),
        output=(result.get("stdout", "") + "\n" + result.get("stderr", "")).strip(),
    )

    # Re-run the health check to see whether the fix actually worked.
    recheck = await supervisor.health_service.run_once(source="post_fix")
    resolved = False
    if recheck["verdict"] == "ok":
        await supervisor.db.update_issue_status(
            issue_id, "resolved", resolution="auto-resolved after fix"
        )
        resolved = True

    # If the fix didn't clear the issue, keep the agent working rather than
    # stopping: propose a different fix (bounded by max_fix_attempts) so the
    # operator gets a fresh fix to try instead of a dead end.
    next_suggestion = None
    if not resolved:
        issue = await supervisor.db.get_issue(issue_id)
        prior = await supervisor.db.list_suggestions_for_issue(issue_id)
        max_attempts = int(
            getattr(
                supervisor.config.autonomous, "max_fix_attempts", fixer.MAX_FIX_ATTEMPTS
            )
        )
        if issue and len(prior) < max_attempts:
            tried: list[tuple[str, str]] = []
            for s in prior:
                if not s.get("command"):
                    continue
                if s.get("status") == "failed":
                    why = f"failed (exit {s.get('exit_code')})"
                else:
                    why = "ran but the issue is still present"
                tried.append((s["command"], why))
            try:
                np = await fixer.propose_fix(issue, supervisor.config, tried=tried)
                nsid = await supervisor.db.add_suggestion(
                    issue_id=issue_id,
                    command=np.command,
                    explanation=np.explanation,
                    risk=np.risk,
                    reversible=np.reversible,
                )
                await supervisor.db.add_audit(
                    action="suggestion_proposed",
                    command=np.command,
                    detail=f"auto after failed fix for issue {issue_id}",
                )
                next_suggestion = await supervisor.db.get_suggestion(nsid)
            except Exception:  # noqa: BLE001 - the next fix is best-effort
                next_suggestion = None

    return {
        "executed": result,
        "recheck": {"verdict": recheck["verdict"], "summary": recheck["summary"]},
        "issue_resolved": resolved,
        "next_suggestion": next_suggestion,
    }


@router.post("/autonomous/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(request: Request, suggestion_id: int) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    suggestion = await supervisor.db.get_suggestion(suggestion_id)
    if not suggestion:
        raise ApiError(404, "not_found", f"No suggestion {suggestion_id}")
    await supervisor.db.update_suggestion(suggestion_id, status="dismissed")
    await supervisor.db.add_audit(
        action="suggestion_dismissed",
        command=suggestion["command"],
        detail=f"suggestion {suggestion_id}",
    )
    updated = await supervisor.db.get_suggestion(suggestion_id)
    return {"suggestion": updated}


@router.get("/autonomous/audit")
async def list_audit(
    request: Request,
    limit: int = 100,
    action: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    limit = max(1, min(limit, 500))
    rows = await supervisor.db.list_audit(
        limit=limit, action=action, actor=actor, since=since, until=until
    )
    return {"audit": rows}


def _enabled_channels(config) -> list[str]:
    n = config.autonomous.notification
    channels: list[str] = []
    if n.enabled and n.email.enabled:
        channels.append("email")
    if n.enabled and n.webhook.enabled:
        channels.append("webhook")
    return channels
