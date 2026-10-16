"""Boot-time disaster recovery handler (spec §9).

Run once at boot (and on demand), this walks a fixed checklist that brings the
host back to a known-good state after a crash or unexpected restart. Every action
is safe and reversible — recovery never does anything a human wouldn't want to
undo. The LLM is not involved: this is a deterministic playbook, because at boot
you want reliability, not improvisation.

Order matters (spec §9.2):
  1. Backups first — if we can't confirm backups, that is itself a CRITICAL
     finding (recovering without a safety net is dangerous).
  2. Failed units — restart the allowlisted ones once, then recheck.
  3. Disk full — vacuum + temp cleanup, then recheck.
  4. Expected services — restart any that are down, then verify.
  5. Kernel filesystem errors — alert immediately (never fsck a live root).

A per-boot guard records which units have already been restarted this boot so a
manual re-run does not restart them again. ``report_only`` turns every restart /
cleanup into a reported no-op.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..db import Database
from ..settings import AppConfig
from .health import HealthChecker, CommandRunner
from .report import TaskReport
from .tools import DaemonContext, _elevated_exec

logger = logging.getLogger(__name__)

# Kernel / filesystem error signatures that mean "stop and alert" — never try to
# repair a live filesystem with these present.
_FS_ERROR_PATTERNS = [
    r"EXT4-fs error",
    r"EXT4-fs \([^)]*\): Remounting filesystem read-only",
    r"Buffer I/O error",
    r"I/O error",
    r"XFS.*(?:error|corrupt)",
    r"btrfs.*(?:error|corrupt)",
    r"forced readonly",
    r"forced read-only",
]
_FS_ERROR_RE = [re.compile(p, re.IGNORECASE) for p in _FS_ERROR_PATTERNS]


def _read_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def _load_attempted(state_path: Path, boot_id: str) -> set[str]:
    """Units already restarted this boot. A different boot id resets the set."""
    if not state_path.exists():
        return set()
    try:
        data = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    if data.get("boot_id") != boot_id:
        return set()
    return set(data.get("attempted_units", []))


def _save_attempted(state_path: Path, boot_id: str, attempted: set[str]) -> None:
    try:
        state_path.write_text(
            json.dumps({"boot_id": boot_id, "attempted_units": sorted(attempted)})
        )
    except OSError as exc:  # noqa: BLE001 — guard is best-effort
        logger.warning("could not write recovery state file: %s", exc)


def _normalize_unit(name: str) -> str:
    """Reduce a systemd unit name to its bare form for allowlist matching.

    ``systemctl --failed`` reports full names (``nginx.service``) while the
    restart allowlist and the sudoers entries use the bare shorthand
    (``nginx``). Stripping a trailing ``.service`` reconciles the two; other
    unit types (``.socket``, ``.mount``) are left as-is.
    """
    n = (name or "").strip()
    if n.endswith(".service"):
        return n[: -len(".service")]
    return n


def _unit_in_allowlist(unit: str, allowlist: list[str]) -> bool:
    norm = _normalize_unit(unit)
    return any(_normalize_unit(a) == norm for a in allowlist)


async def _get_failed_units(runner: CommandRunner) -> list[str]:
    try:
        rc, out, _err = await runner(["systemctl", "--failed", "--no-legend", "--no-pager"])
    except (FileNotFoundError, OSError):
        return []
    if rc != 0:
        return []
    return [ln.split()[0] for ln in out.splitlines() if ln.strip()]


async def _is_active(runner: CommandRunner, service: str) -> bool:
    try:
        rc, out, _err = await runner(["systemctl", "is-active", service])
    except (FileNotFoundError, OSError):
        return False
    return (out or "").strip() == "active"


async def _check_kernel_fs_errors(runner: CommandRunner) -> str:
    """Return a non-empty description if the journal shows filesystem errors."""
    try:
        rc, out, _err = await runner(["journalctl", "-p", "err", "-n", "500", "--no-pager"])
    except (FileNotFoundError, OSError):
        return ""
    if rc != 0:
        return ""
    hits = [ln for ln in out.splitlines() if any(rx.search(ln) for rx in _FS_ERROR_RE)]
    if not hits:
        return ""
    return f"{len(hits)} filesystem error(s): " + "; ".join(h[:120] for h in hits[:5])


async def _restart_unit(unit: str, runner: CommandRunner, db: Database) -> dict[str, Any]:
    """Restart one unit via ``sudo -n`` and audit it.

    The unit's eligibility is gated by ``recovery.restart_allowlist`` (checked by
    the caller) and by the OS sudoers file — not by the general command
    allowlist. The restart allowlist *is* the allowlist for this action, so the
    operator configures it in one place.
    """
    argv = ["/usr/bin/systemctl", "restart", unit]
    command_str = " ".join(argv)
    try:
        rc, out, err = await runner(["sudo", "-n", *argv])
    except Exception as exc:  # noqa: BLE001
        await db.add_audit(
            action="command_error",
            command=command_str,
            detail=f"recovery:restart:{unit}: {exc}",
            actor="daemon",
        )
        return {"ok": False, "error": str(exc)}
    await db.add_audit(
        action="command_executed",
        command=command_str,
        exit_code=rc,
        detail=f"recovery:restart:{unit}",
        actor="daemon",
    )
    return {"ok": rc == 0, "exit_code": rc}


async def run_recovery(
    config: AppConfig,
    db: Database,
    notifier,
    runner: CommandRunner | None = None,
) -> TaskReport:
    """Run one boot-recovery pass. Returns a :class:`TaskReport`."""
    report_only = bool(config.autonomous.report_only)
    rec = config.autonomous.recovery
    vacuum_size = config.autonomous.maintenance.vacuum_size
    checker = HealthChecker(config, runner=runner)
    dctx = DaemonContext(config=config, db=db, report_only=report_only, runner=runner)

    critical: list[str] = []
    warnings: list[str] = []
    actions: list[dict[str, Any]] = []

    # 1. Backups first.
    backup = await checker.check_backup_freshness()
    if backup.status != "ok":
        critical.append(f"backups: {backup.message}")

    # 2. Failed units -> restart allowlisted ones once.
    boot_id = _read_boot_id()
    state_path = db.path.parent / "recovery_state.json"
    attempted = _load_attempted(state_path, boot_id)
    failed = await _get_failed_units(runner)
    to_restart = [
        _normalize_unit(u)
        for u in failed
        if _unit_in_allowlist(u, rec.restart_allowlist)
        and _normalize_unit(u) not in attempted
    ]

    if report_only:
        for u in to_restart:
            actions.append({"action": "would_restart_unit", "unit": u})
    else:
        for u in to_restart:
            res = await _restart_unit(u, runner, db)
            attempted.add(u)
            if res.get("ok"):
                actions.append({"action": "restarted_unit", "unit": u})
            else:
                warnings.append(f"restart {u} failed: {res.get('error') or res.get('exit_code')}")
        _save_attempted(state_path, boot_id, attempted)

    # Recheck failed units after restarts.
    still_failed = await _get_failed_units(runner)
    if still_failed:
        warnings.append(f"units still failed: {', '.join(still_failed)}")

    # 3. Disk full -> vacuum + temp cleanup -> recheck.
    disk = await checker.check_disk()
    if disk.status == "critical":
        if report_only:
            actions.append({"action": "would_cleanup_disk"})
        else:
            await _elevated_exec(
                ["/usr/bin/journalctl", f"--vacuum-size={vacuum_size}"],
                dctx,
                "recovery:vacuum",
            )
            await _elevated_exec(
                ["/opt/claudio/scripts/cleanup_tmp.sh"], dctx, "recovery:temp_cleanup"
            )
            disk = await checker.check_disk()
            if disk.status == "critical":
                critical.append(f"disk still full after cleanup: {disk.message}")

    # 4. Expected services down -> restart -> verify.
    for svc in rec.expected_services:
        if await _is_active(runner, svc):
            continue
        if report_only:
            actions.append({"action": "would_restart_service", "service": svc})
            critical.append(f"expected service {svc} is down (report-only, not restarted)")
            continue
        await _restart_unit(svc, runner, db)
        if await _is_active(runner, svc):
            actions.append({"action": "restarted_service", "service": svc})
        else:
            critical.append(f"expected service {svc} still down after restart")

    # 5. Kernel filesystem errors -> alert immediately.
    fs_errors = await _check_kernel_fs_errors(runner)
    if fs_errors:
        critical.append(fs_errors)

    if critical:
        verdict = "critical"
    elif warnings:
        verdict = "warning"
    else:
        verdict = "ok"

    summary = (
        "recovery: all clear"
        if verdict == "ok"
        else f"recovery: {len(critical)} critical, {len(warnings)} warning"
    )

    report = TaskReport(
        verdict=verdict,
        summary=summary,
        report=_render_report(
            actions=actions,
            critical=critical,
            warnings=warnings,
            backup=backup,
            disk=disk,
            report_only=report_only,
        ),
        details={
            "actions": actions,
            "critical": critical,
            "warnings": warnings,
            "backup": backup.to_dict(),
            "disk": disk.to_dict(),
            "report_only": report_only,
        },
    )

    if verdict != "ok":
        await notifier.notify(verdict, summary, [
            {"name": "recovery", "status": verdict, "message": summary}
        ])

    return report


def _render_report(
    *,
    actions: list[dict[str, Any]],
    critical: list[str],
    warnings: list[str],
    backup: Any,
    disk: Any,
    report_only: bool,
) -> str:
    lines = ["Recovery report", "=" * 40]
    if report_only:
        lines.append("(report-only mode: no restarts or cleanups were performed)")
    lines.append(f"Backups: {backup.status} — {backup.message}")
    lines.append(f"Disk: {disk.status} — {disk.message}")
    if actions:
        lines.append("Actions:")
        for a in actions:
            label = a.get("action", "?")
            target = a.get("unit") or a.get("service") or ""
            lines.append(f"  - {label} {target}".rstrip())
    if critical:
        lines.append("CRITICAL:")
        for c in critical:
            lines.append(f"  - {c}")
    if warnings:
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)
