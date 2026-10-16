"""Tests for the scoped daemon tools: report-only refusal, allowlist gating,
argument validation, and audit on every mutation.
"""
from __future__ import annotations

import pytest

from agents import RunContextWrapper

from claudio.settings import AppConfig
from claudio.db import Database
from claudio.autonomous.tools import (
    DaemonContext,
    build_daemon_tools,
    effective_report_only,
    run_updates,
    autoremove,
    vacuum_logs,
    restart_service,
    check_disk,
)


async def _make_ctx(tmp_path, report_only=False, runner=None, restart_allowlist=None, vacuum_size="200M"):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = AppConfig()
    cfg.autonomous.maintenance.vacuum_size = vacuum_size
    if restart_allowlist is not None:
        cfg.autonomous.recovery.restart_allowlist = restart_allowlist
    return DaemonContext(config=cfg, db=db, report_only=report_only, runner=runner)


def _wrap(dctx):
    return RunContextWrapper(context=dctx)


async def _ok_runner(cmd):
    return (0, "done", "")


# ---- build_daemon_tools ------------------------------------------------


def test_build_daemon_tools_report_only_has_no_mutations():
    tools = build_daemon_tools(report_only=True)
    names = {t.name for t in tools}
    assert names == {"check_disk", "check_memory", "check_services", "check_logs", "verify_backups"}


def test_build_daemon_tools_full_has_mutations():
    tools = build_daemon_tools(report_only=False)
    names = {t.name for t in tools}
    assert "run_updates" in names
    assert "restart_service" in names
    assert len(names) == 9


# ---- report-only refusal -----------------------------------------------


async def test_run_updates_refused_in_report_only(tmp_path):
    dctx = await _make_ctx(tmp_path, report_only=True, runner=_ok_runner)
    res = await run_updates(_wrap(dctx))
    assert res["ok"] is False
    assert res.get("refused") is True
    # The runner must NOT have been called.
    audit = await dctx.db.list_audit()
    assert any(a["action"] == "mutation_refused_report_only" for a in audit)


async def test_vacuum_refused_in_report_only(tmp_path):
    dctx = await _make_ctx(tmp_path, report_only=True, runner=_ok_runner)
    res = await vacuum_logs(_wrap(dctx))
    assert res.get("refused") is True


# ---- allowlist gating --------------------------------------------------


async def test_run_updates_denied_when_not_in_allowlist(tmp_path):
    dctx = await _make_ctx(tmp_path, report_only=False, runner=_ok_runner)
    # Override the allowlist to something that does NOT include apt-get update.
    dctx.config.autonomous.command_allowlist = ["/usr/bin/df -P"]
    res = await run_updates(_wrap(dctx))
    assert res["ok"] is False
    assert "not in allowlist" in res["error"]
    audit = await dctx.db.list_audit()
    assert any(a["action"] == "command_denied" for a in audit)


async def test_run_updates_executes_when_allowed(tmp_path):
    calls = []

    async def spy_runner(cmd):
        calls.append(cmd)
        return (0, "updated", "")

    dctx = await _make_ctx(tmp_path, report_only=False, runner=spy_runner)
    res = await run_updates(_wrap(dctx))
    assert res["ok"] is True
    assert calls == [["sudo", "-n", "/usr/bin/apt-get", "update"]]
    audit = await dctx.db.list_audit()
    assert any(a["action"] == "command_executed" and a["exit_code"] == 0 for a in audit)


# ---- restart_service validation ----------------------------------------


async def test_restart_service_rejects_unlisted_unit(tmp_path):
    dctx = await _make_ctx(tmp_path, report_only=False, runner=_ok_runner, restart_allowlist=["nginx"])
    res = await restart_service(_wrap(dctx), name="redis")
    assert res["ok"] is False
    assert "not in the restart allowlist" in res["error"]


async def test_restart_service_rejects_bad_format(tmp_path):
    dctx = await _make_ctx(tmp_path, report_only=False, runner=_ok_runner, restart_allowlist=["nginx"])
    res = await restart_service(_wrap(dctx), name="bad name")
    assert res["ok"] is False


async def test_restart_service_ok_when_listed(tmp_path):
    calls = []

    async def spy_runner(cmd):
        calls.append(cmd)
        return (0, "", "")

    dctx = await _make_ctx(
        tmp_path,
        report_only=False,
        runner=spy_runner,
        restart_allowlist=["nginx"],
    )
    # The restart command must also be in the command allowlist.
    dctx.config.autonomous.command_allowlist = ["/usr/bin/systemctl restart nginx"]
    res = await restart_service(_wrap(dctx), name="nginx")
    assert res["ok"] is True
    assert calls == [["sudo", "-n", "/usr/bin/systemctl", "restart", "nginx"]]


# ---- read-only tool passthrough ----------------------------------------


async def test_check_disk_returns_status(tmp_path):
    async def runner(cmd):
        return (0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 50 50 50% /\n", "")

    dctx = await _make_ctx(tmp_path, runner=runner)
    res = await check_disk(_wrap(dctx))
    assert res["status"] == "ok"


# ---- effective_report_only ---------------------------------------------


def test_effective_report_only_or_composition():
    assert effective_report_only(True, False) is True
    assert effective_report_only(False, True) is True
    assert effective_report_only(False, False) is False
    assert effective_report_only(True, True) is True
