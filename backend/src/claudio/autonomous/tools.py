"""Scoped admin tools for the daemon role (spec §10.3).

Each tool is a named, validated action that maps to one allowlisted command. The
daemon never freeforms — there is no ``run_command`` here. Read-only tools inspect
the host; mutation tools validate their arguments, check the command allowlist and
the report-only flag, then run the exact command via ``sudo -n`` and audit it.

This is the real safety boundary the spec §4 asks for: the model cannot emit an
arbitrary shell string because no tool accepts one. Combined with
:func:`build_daemon_tools`, which omits the mutation tools entirely under
report-only mode, the daemon can only ever do what is explicitly allowed.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agents import RunContextWrapper, function_tool

from ..db import Database, utcnow
from ..settings import AppConfig
from . import allowlist
from .health import CheckResult, HealthChecker, CommandRunner

# Generous per-command ceiling for elevated maintenance operations (apt can be slow).
ELEVATED_TIMEOUT = 600.0


@dataclass
class DaemonContext:
    """Runtime context handed to every daemon tool via the agent's run context."""

    config: AppConfig
    db: Database
    report_only: bool
    runner: CommandRunner | None = None

    @property
    def checker(self) -> HealthChecker:
        return HealthChecker(self.config, runner=self.runner)


async def _elevated_runner(cmd: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ELEVATED_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return (-1, "", f"command timed out after {ELEVATED_TIMEOUT}s")
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout.decode(errors="replace") if stdout else "",
        stderr.decode(errors="replace") if stderr else "",
    )


def _clip(text: str, max_chars: int = 8000) -> str:
    if len(text) > max_chars:
        return text[:max_chars] + f"\n[clipped: {len(text) - max_chars} chars omitted]"
    return text


async def _elevated_exec(argv: list[str], ctx: DaemonContext, action: str) -> dict[str, Any]:
    """Validate, gate, run (``sudo -n``) and audit one elevated command.

    ``argv`` is the inner command WITHOUT the ``sudo`` prefix. The allowlist and
    the sudoers file both refer to that inner command; ``sudo -n`` is prepended at
    exec time.
    """
    command_str = " ".join(argv)

    # 1. Report-only: refuse before doing anything.
    if ctx.report_only:
        await ctx.db.add_audit(
            action="mutation_refused_report_only",
            command=command_str,
            detail=action,
            actor="daemon",
        )
        return {
            "ok": False,
            "refused": True,
            "reason": "report_only mode: mutations are disabled",
        }

    # 2. Allowlist: the command must be explicitly permitted.
    if not allowlist.is_allowed(command_str, ctx.config):
        await ctx.db.add_audit(
            action="command_denied",
            command=command_str,
            detail=f"{action}: not in allowlist",
            actor="daemon",
        )
        return {"ok": False, "error": f"command not in allowlist: {command_str}"}

    # 3. Execute via scoped sudo (non-interactive).
    runner = ctx.runner or _elevated_runner
    try:
        rc, out, err = await runner(["sudo", "-n", *argv])
    except Exception as exc:  # noqa: BLE001
        await ctx.db.add_audit(
            action="command_error",
            command=command_str,
            detail=f"{action}: {exc}",
            actor="daemon",
        )
        return {"ok": False, "error": f"exec failed: {exc}"}

    # 4. Audit the executed command.
    await ctx.db.add_audit(
        action="command_executed",
        command=command_str,
        exit_code=rc,
        detail=action,
        actor="daemon",
    )
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "stdout": _clip(out),
        "stderr": _clip(err),
        "summary": f"{action}: exit {rc}",
    }


# ---- read-only tools ---------------------------------------------------


async def check_disk(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Check filesystem disk usage and report any filesystem over threshold."""
    res = await ctx.context.checker.check_disk()
    return {
        "ok": res.status == "ok",
        "status": res.status,
        "summary": res.message,
        "value": res.value,
        "threshold": res.threshold,
    }


async def check_memory(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Check memory and swap pressure."""
    res = await ctx.context.checker.check_memory()
    return {
        "ok": res.status == "ok",
        "status": res.status,
        "summary": res.message,
        "value": res.value,
        "threshold": res.threshold,
    }


async def check_services(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Check for failed systemd units."""
    res = await ctx.context.checker.check_failed_units()
    return {
        "ok": res.status == "ok",
        "status": res.status,
        "summary": res.message,
        "detail": res.detail,
    }


async def check_logs(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Check the system journal for recent error-level entries."""
    res = await ctx.context.checker.check_journal_errors()
    return {
        "ok": res.status == "ok",
        "status": res.status,
        "summary": res.message,
        "detail": res.detail,
    }


async def verify_backups(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Check that backups exist and are fresh (see the backup_path config)."""
    res = await ctx.context.checker.check_backup_freshness()
    return {
        "ok": res.status == "ok",
        "status": res.status,
        "summary": res.message,
        "value": res.value,
        "threshold": res.threshold,
    }


# ---- mutation tools ----------------------------------------------------


async def run_updates(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Update the apt package index (``apt-get update``). Safe, reversible."""
    return await _elevated_exec(["/usr/bin/apt-get", "update"], ctx.context, "run_updates")


async def autoremove(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Remove unused packages (``apt-get -y autoremove``). Irreversible."""
    return await _elevated_exec(
        ["/usr/bin/apt-get", "-y", "autoremove"], ctx.context, "autoremove"
    )


async def vacuum_logs(ctx: RunContextWrapper[DaemonContext]) -> dict:
    """Vacuum the journal to the configured size (``journalctl --vacuum-size``)."""
    size = ctx.context.config.autonomous.maintenance.vacuum_size
    return await _elevated_exec(
        ["/usr/bin/journalctl", f"--vacuum-size={size}"], ctx.context, "vacuum_logs"
    )


async def restart_service(ctx: RunContextWrapper[DaemonContext], name: str) -> dict:
    """Restart a systemd unit. The unit must be in the restart allowlist.

    Args:
        name: The systemd unit name to restart (e.g. "nginx").
    """
    try:
        allowlist.validate_service_name(name, ctx.context.config)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return await _elevated_exec(
        ["/usr/bin/systemctl", "restart", name], ctx.context, f"restart_service({name})"
    )


READ_ONLY_TOOLS = [check_disk, check_memory, check_services, check_logs, verify_backups]
MUTATION_TOOLS = [run_updates, autoremove, vacuum_logs, restart_service]


def build_daemon_tools(report_only: bool, strict_mode: bool = False) -> list:
    """Build the daemon's tool set.

    Under report-only mode only the read-only tools are returned — the agent
    cannot call a mutation tool it does not have. Otherwise the full set is
    returned. Approval is always off for the daemon: the allowlist + report-only
    are the boundary, not the SDK approval gate.
    """
    tools = [
        function_tool(fn, name_override=fn.__name__, needs_approval=False, strict_mode=strict_mode)
        for fn in READ_ONLY_TOOLS
    ]
    if not report_only:
        tools.extend(
            function_tool(fn, name_override=fn.__name__, needs_approval=False, strict_mode=strict_mode)
            for fn in MUTATION_TOOLS
        )
    return tools


def effective_report_only(global_flag: bool, task_flag: bool) -> bool:
    """Either flag true means report-only (conservative composition)."""
    return bool(global_flag) or bool(task_flag)
