"""Scheduled maintenance handler (spec §8).

A deterministic, config-driven playbook. No LLM improvisation: the set of
actions is fixed by the config, each action is either *safe* (run directly) or
*irreversible* (routed to the suggestion/accept flow so a human confirms the
exact command first). This is the "the LLM interprets, it does not improvise
privileged commands" rule from spec §3 applied to the maintenance path.

Safe actions (``apt-get update``, journal vacuum, temp cleanup) run through the
same ``sudo -n`` + allowlist + audit gate the daemon tools use
(:func:`claudio.autonomous.tools._elevated_exec`). Irreversible actions
(``autoremove``, full ``upgrade``, ``docker system prune``) never run here —
they become suggestions awaiting operator acceptance.

The handler is fully injectable: pass a ``runner`` to drive every subprocess in
tests without touching a real host.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Database
from ..settings import AppConfig
from . import allowlist
from .health import HealthChecker, CommandRunner
from .report import TaskReport
from .tools import DaemonContext, _elevated_exec

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceAction:
    """One maintenance step: the exact command, and whether it is safe to run
    automatically or must be confirmed (irreversible)."""

    name: str
    argv: list[str]
    safe: bool
    explanation: str


def build_actions(config: AppConfig) -> list[MaintenanceAction]:
    """Build the ordered action list from the maintenance config.

    Only actions whose per-action toggle is on are included. Safe actions come
    first (cheap, low-risk cleanup), then the irreversible ones (which become
    suggestions).
    """
    m = config.autonomous.maintenance
    safe: list[MaintenanceAction] = []
    irreversible: list[MaintenanceAction] = []

    if m.apt_update:
        safe.append(MaintenanceAction(
            name="apt_update",
            argv=["/usr/bin/apt-get", "update"],
            safe=True,
            explanation="Refresh the apt package index.",
        ))
    if m.vacuum_logs:
        safe.append(MaintenanceAction(
            name="vacuum_logs",
            argv=["/usr/bin/journalctl", f"--vacuum-size={m.vacuum_size}"],
            safe=True,
            explanation=f"Trim the systemd journal to {m.vacuum_size}.",
        ))
    if m.temp_cleanup:
        safe.append(MaintenanceAction(
            name="temp_cleanup",
            argv=["/opt/claudio/scripts/cleanup_tmp.sh"],
            safe=True,
            explanation="Remove stale files from " + ", ".join(m.temp_dirs) + ".",
        ))
    if m.autoremove:
        irreversible.append(MaintenanceAction(
            name="autoremove",
            argv=["/usr/bin/apt-get", "-y", "autoremove"],
            safe=False,
            explanation="Remove packages no longer required. Irreversible.",
        ))
    if m.full_upgrade:
        irreversible.append(MaintenanceAction(
            name="full_upgrade",
            argv=["/usr/bin/apt-get", "-y", "upgrade"],
            safe=False,
            explanation="Upgrade all installed packages. Irreversible; may restart services.",
        ))
    if m.docker_prune:
        irreversible.append(MaintenanceAction(
            name="docker_prune",
            argv=["/usr/bin/docker", "system", "prune", "-f"],
            safe=False,
            explanation="Remove unused Docker images, stopped containers, networks and build cache. Irreversible.",
        ))
    return safe + irreversible


async def check_unattended_upgrades(runner: CommandRunner) -> dict[str, Any]:
    """Report whether the OS's own unattended-upgrades service is enabled.

    Spec §8 prefers the OS mechanism for security updates. This is informational:
    if it is enabled, a manual full upgrade is largely redundant. Never escalates
    the verdict on its own.
    """
    try:
        rc, out, _err = await runner(["systemctl", "is-enabled", "unattended-upgrades"])
    except (FileNotFoundError, OSError) as exc:
        return {"checked": False, "enabled": None, "note": f"systemctl unavailable: {exc}"}
    state = (out or "").strip()
    enabled = state == "enabled"
    return {
        "checked": True,
        "enabled": enabled,
        "state": state or "unknown",
        "note": (
            "OS unattended-upgrades is active — security patches apply automatically"
            if enabled
            else "unattended-upgrades is not active; security updates need manual/apt handling"
        ),
    }


async def run_maintenance(
    config: AppConfig,
    db: Database,
    notifier,
    runner: CommandRunner | None = None,
) -> TaskReport:
    """Run one maintenance pass. Returns a :class:`TaskReport`.

    ``report_only`` (from ``config.autonomous.report_only``) makes every safe
    action a recorded no-op ("would run"); irreversible actions still become
    suggestions, since a suggestion mutates nothing.
    """
    report_only = bool(config.autonomous.report_only)
    m = config.autonomous.maintenance
    actions = build_actions(config)

    # A DaemonContext gives the safe actions the exact same allowlist + sudo -n +
    # audit gate the daemon tools use.
    dctx = DaemonContext(config=config, db=db, report_only=report_only, runner=runner)

    executed: list[dict[str, Any]] = []
    would_run: list[str] = []
    suggested: list[dict[str, Any]] = []
    failures: list[str] = []

    for action in actions:
        command_str = " ".join(action.argv)
        if action.safe:
            result = await _elevated_exec(action.argv, dctx, f"maintenance:{action.name}")
            if result.get("refused"):
                # report_only: safe action skipped by design, not a failure.
                would_run.append(action.name)
            elif not result.get("ok"):
                failures.append(f"{action.name}: {result.get('error') or ('exit ' + str(result.get('exit_code')))}")
            else:
                executed.append({"name": action.name, "exit_code": result.get("exit_code", 0)})
        else:
            sid = await db.add_suggestion(
                issue_id=None,
                command=f"sudo {command_str}",
                explanation=action.explanation,
                risk="high",
                reversible=False,
            )
            suggested.append({
                "name": action.name,
                "suggestion_id": sid,
                "command": f"sudo {command_str}",
            })
            await db.add_audit(
                action="maintenance_suggested",
                command=command_str,
                detail=f"maintenance:{action.name}",
                actor="daemon",
            )

    # Deterministic post-checks.
    unattended = await check_unattended_upgrades(runner or _elevated_runner_default())
    disk_after = await HealthChecker(config, runner=runner).check_disk()

    # Audit retention sweep.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=m.audit_retention_days)).isoformat()
    try:
        removed = await db.delete_audit_older_than(cutoff)
    except Exception as exc:  # noqa: BLE001 — retention is best-effort
        removed = 0
        logger.warning("audit retention sweep failed: %s", exc)

    # Verdict: disk still critical after cleanup dominates; else any failure is a
    # warning; else all clear.
    if disk_after.status == "critical":
        verdict = "critical"
        summary = f"maintenance: disk still full after cleanup ({disk_after.message})"
    elif failures:
        verdict = "warning"
        summary = f"maintenance: {len(failures)} action(s) failed: " + "; ".join(failures)
    else:
        verdict = "ok"
        summary = (
            f"maintenance: {len(executed)} action(s) run, "
            f"{len(suggested)} suggestion(s)"
        )

    report = TaskReport(
        verdict=verdict,
        summary=summary,
        report=_render_report(
            executed=executed,
            would_run=would_run,
            suggested=suggested,
            failures=failures,
            unattended=unattended,
            disk_after=disk_after,
            audit_removed=removed,
            report_only=report_only,
        ),
        details={
            "executed": executed,
            "would_run": would_run,
            "suggested": suggested,
            "failures": failures,
            "unattended_upgrades": unattended,
            "disk_after": disk_after.to_dict(),
            "audit_removed": removed,
            "report_only": report_only,
        },
    )

    # Non-OK maintenance runs alert the operator (OK runs stay silent, matching
    # the health-service fatigue rule).
    if verdict in ("warning", "critical"):
        await notifier.notify(verdict, summary, [
            {"name": "maintenance", "status": verdict, "message": summary}
        ])

    return report


def _elevated_runner_default() -> CommandRunner:
    from .tools import _elevated_runner

    return _elevated_runner


def _render_report(
    *,
    executed: list[dict[str, Any]],
    would_run: list[str],
    suggested: list[dict[str, Any]],
    failures: list[str],
    unattended: dict[str, Any],
    disk_after: Any,
    audit_removed: int,
    report_only: bool,
) -> str:
    lines = ["Maintenance report", "=" * 40]
    if report_only:
        lines.append("(report-only mode: no host changes were made)")
    if executed:
        lines.append("Ran:")
        for e in executed:
            lines.append(f"  - {e['name']} (exit {e['exit_code']})")
    if would_run:
        lines.append("Would run (skipped, report-only):")
        for w in would_run:
            lines.append(f"  - {w}")
    if suggested:
        lines.append("Awaiting confirmation (irreversible):")
        for s in suggested:
            lines.append(f"  - {s['name']}: {s['command']}")
    if failures:
        lines.append("Failures:")
        for f in failures:
            lines.append(f"  - {f}")
    lines.append(f"unattended-upgrades: {unattended.get('note', 'unknown')}")
    lines.append(f"Disk after cleanup: {disk_after.status} — {disk_after.message}")
    lines.append(f"Audit rows removed (retention): {audit_removed}")
    return "\n".join(lines)
