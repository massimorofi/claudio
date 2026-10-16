"""Tests for the Phase 1 autonomous watch layer: health checks, verdict,
storage, issue de-duplication, and notification dispatch.
"""
from __future__ import annotations

import os
import types

import pytest

from claudio.settings import AppConfig
from claudio.db import Database
from claudio.autonomous import health as h
from claudio.autonomous.health import (
    CheckResult,
    HealthChecker,
    overall_verdict,
    build_summary,
    run_health_checks,
)
from claudio.autonomous.service import HealthService


# ---- verdict / summary helpers -----------------------------------------


def test_overall_verdict_worst_wins():
    assert overall_verdict([CheckResult("a", "ok", "")]) == "ok"
    assert overall_verdict(
        [CheckResult("a", "ok", ""), CheckResult("b", "warning", "")]
    ) == "warning"
    assert overall_verdict(
        [CheckResult("a", "warning", ""), CheckResult("b", "critical", "")]
    ) == "critical"
    # skipped never escalates
    assert overall_verdict(
        [CheckResult("a", "ok", ""), CheckResult("b", "skipped", "")]
    ) == "ok"


def test_build_summary_lists_bad_checks():
    results = [
        CheckResult("disk", "warning", "/ at 90%"),
        CheckResult("load", "ok", "fine"),
    ]
    s = build_summary(results)
    assert "disk: / at 90%" in s
    assert "load" not in s
    assert build_summary([CheckResult("a", "ok", "")]) == "All checks passed"


# ---- disk --------------------------------------------------------------


def _df_output(rows):
    header = "Filesystem     1024-blocks    Used Available Capacity Mounted on"
    return header + "\n" + "\n".join(rows)


async def test_check_disk_ok():
    async def runner(cmd):
        return (0, _df_output(["/dev/sda1 100000000 50000000 50000000 50% /"]), "")

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_disk()
    assert res.status == "ok"


async def test_check_disk_warning():
    async def runner(cmd):
        return (0, _df_output(["/dev/sda1 100000000 91000000 9000000 91% /"]), "")

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_disk()
    assert res.status == "warning"
    assert "/ at 91%" in res.message


async def test_check_disk_critical():
    async def runner(cmd):
        return (0, _df_output(["/dev/sda1 100000000 97000000 3000000 97% /"]), "")

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_disk()
    assert res.status == "critical"


async def test_check_disk_skips_pseudo_filesystems():
    # tmpfs at 99% must not trigger — it's volatile pseudo storage.
    async def runner(cmd):
        return (
            0,
            _df_output(
                [
                    "/dev/sda1 100000000 50000000 50000000 50% /",
                    "tmpfs 1000000 990000 10000 99% /run",
                ]
            ),
            "",
        )

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_disk()
    assert res.status == "ok"


async def test_check_disk_skipped_when_df_fails():
    async def runner(cmd):
        raise FileNotFoundError("no df")

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_disk()
    assert res.status == "skipped"


# ---- memory ------------------------------------------------------------


async def test_check_memory_ok(monkeypatch):
    monkeypatch.setattr(
        h, "_read_meminfo",
        lambda: {"MemTotal": 10000000, "MemAvailable": 6000000, "SwapTotal": 0, "SwapFree": 0},
    )
    cfg = AppConfig()
    res = await HealthChecker(cfg).check_memory()
    assert res.status == "ok"


async def test_check_memory_swap_warning(monkeypatch):
    monkeypatch.setattr(
        h, "_read_meminfo",
        lambda: {"MemTotal": 10000000, "MemAvailable": 5000000, "SwapTotal": 1000000, "SwapFree": 100000},
    )
    cfg = AppConfig()  # swap_warn_percent default 80
    res = await HealthChecker(cfg).check_memory()
    assert res.status == "warning"


async def test_check_memory_critical(monkeypatch):
    monkeypatch.setattr(
        h, "_read_meminfo",
        lambda: {"MemTotal": 10000000, "MemAvailable": 100000, "SwapTotal": 0, "SwapFree": 0},
    )
    cfg = AppConfig()  # mem_crit_percent default 95
    res = await HealthChecker(cfg).check_memory()
    assert res.status == "critical"


# ---- load --------------------------------------------------------------


async def test_check_load_ok(monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (0.5, 0.5, 0.5))
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    cfg = AppConfig()
    res = await HealthChecker(cfg).check_load()
    assert res.status == "ok"


async def test_check_load_warning(monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (9.0, 9.0, 9.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 8)  # load1 > 8*1.0
    cfg = AppConfig()
    res = await HealthChecker(cfg).check_load()
    assert res.status == "warning"


async def test_check_load_critical(monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (17.0, 17.0, 17.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 8)  # load1 > 8*2.0
    cfg = AppConfig()
    res = await HealthChecker(cfg).check_load()
    assert res.status == "critical"


# ---- failed units / journal --------------------------------------------


async def test_check_failed_units_warning():
    async def runner(cmd):
        return (0, "nginx.service loaded failed failed\npostgres.service loaded failed failed\n", "")

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_failed_units()
    assert res.status == "warning"
    assert "2 failed unit(s)" in res.message


async def test_check_failed_units_ok():
    async def runner(cmd):
        return (0, "", "")

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_failed_units()
    assert res.status == "ok"


async def test_check_journal_errors_warning():
    async def runner(cmd):
        return (0, "err line 1\nerr line 2\nerr line 3\n", "")

    cfg = AppConfig()
    res = await HealthChecker(cfg, runner=runner).check_journal_errors()
    assert res.status == "warning"
    assert "3 error-level" in res.message


# ---- backup freshness --------------------------------------------------


async def test_backup_missing_path_skipped():
    cfg = AppConfig()  # backup_path empty
    res = await HealthChecker(cfg).check_backup_freshness()
    assert res.status == "skipped"


async def test_backup_nonexistent_critical():
    cfg = AppConfig()
    cfg.autonomous.health_check.backup_path = "/nonexistent/backup/dir"
    res = await HealthChecker(cfg).check_backup_freshness()
    assert res.status == "critical"


async def test_backup_empty_critical(tmp_path):
    cfg = AppConfig()
    cfg.autonomous.health_check.backup_path = str(tmp_path)
    res = await HealthChecker(cfg).check_backup_freshness()
    assert res.status == "critical"  # no files


async def test_backup_fresh_ok(tmp_path):
    cfg = AppConfig()
    cfg.autonomous.health_check.backup_path = str(tmp_path)
    f = tmp_path / "backup.tar"
    f.write_text("x")
    res = await HealthChecker(cfg).check_backup_freshness()
    assert res.status == "ok"


async def test_backup_stale_warning(tmp_path):
    cfg = AppConfig()
    cfg.autonomous.health_check.backup_path = str(tmp_path)
    cfg.autonomous.health_check.backup_max_age_hours = 1.0
    f = tmp_path / "old.tar"
    f.write_text("x")
    old = (os.stat(f).st_atime, os.stat(f).st_mtime - 7200)  # 2h old
    os.utime(f, old)
    res = await HealthChecker(cfg).check_backup_freshness()
    assert res.status == "warning"


# ---- run_health_checks aggregate ---------------------------------------


async def test_run_health_checks_returns_dict():
    async def runner(cmd):
        if cmd[0] == "df":
            return (0, _df_output(["/dev/sda1 100000000 50000000 50000000 50% /"]), "")
        return (0, "", "")

    cfg = AppConfig()
    result = await run_health_checks(cfg, runner=runner)
    assert result["verdict"] in ("ok", "warning", "critical")
    assert isinstance(result["checks"], list)
    assert result["duration_ms"] >= 0


# ---- HealthService: record + issue + notify ----------------------------


class FakeNotifier:
    def __init__(self):
        self.calls = []

    async def notify(self, verdict, summary, details=None):
        self.calls.append((verdict, summary))

    async def test(self):
        return {"ok": True}


def _warning_runner():
    async def runner(cmd):
        if cmd[0] == "df":
            return (0, _df_output(["/dev/sda1 100000000 97000000 3000000 97% /"]), "")
        return (0, "", "")
    return runner


async def test_service_records_check(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = AppConfig()
    svc = HealthService(cfg, db, notifier=FakeNotifier(), runner=_warning_runner())
    result = await svc.run_once("manual")
    assert result["verdict"] == "critical"
    assert result["id"] >= 1
    latest = await db.get_latest_health_check()
    assert latest["verdict"] == "critical"
    await db.close()


async def test_service_creates_issue_on_bad_verdict(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = AppConfig()
    fake = FakeNotifier()
    svc = HealthService(cfg, db, notifier=fake, runner=_warning_runner())
    result = await svc.run_once("scheduled")
    assert result["new_issue"] is not None
    open_issues = await db.list_issues(status="open")
    assert len(open_issues) == 1
    assert open_issues[0]["severity"] == "critical"
    # notification fired once
    assert len(fake.calls) == 1
    await db.close()


async def test_service_dedupes_open_issue(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = AppConfig()
    fake = FakeNotifier()
    svc = HealthService(cfg, db, notifier=fake, runner=_warning_runner())
    await svc.run_once("scheduled")
    await svc.run_once("scheduled")  # same problem again
    open_issues = await db.list_issues(status="open")
    assert len(open_issues) == 1  # not duplicated
    # notified only once (the second run found the open issue)
    assert len(fake.calls) == 1
    await db.close()


async def test_service_no_issue_when_ok(tmp_path, monkeypatch):
    # Control the verdict directly: the check logic is tested separately; here we
    # verify the service's record/issue/notify behavior on an all-clear result.
    async def fake_checks(config, runner=None):
        return {
            "verdict": "ok",
            "summary": "All checks passed",
            "duration_ms": 5,
            "checks": [{"name": "disk", "status": "ok", "message": "fine"}],
        }

    monkeypatch.setattr("claudio.autonomous.service.run_health_checks", fake_checks)
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = AppConfig()
    fake = FakeNotifier()
    svc = HealthService(cfg, db, notifier=fake)
    result = await svc.run_once("scheduled")
    assert result["verdict"] == "ok"
    assert result["new_issue"] is None
    assert len(fake.calls) == 0
    await db.close()


# ---- notifier channel logic --------------------------------------------


def test_notifier_enabled_channels():
    cfg = AppConfig()
    from claudio.autonomous.notifier import Notifier
    n = Notifier(cfg)
    assert n.enabled_channels() == []  # notification disabled by default
    cfg.autonomous.notification.enabled = True
    cfg.autonomous.notification.email.enabled = True
    assert "email" in n.enabled_channels()
    cfg.autonomous.notification.webhook.enabled = True
    assert set(n.enabled_channels()) == {"email", "webhook"}


def test_notifier_email_render():
    from claudio.autonomous.notifier import Notifier
    cfg = AppConfig()
    n = Notifier(cfg)
    body = n._render_email(
        "critical",
        "disk full",
        [{"name": "disk", "status": "critical", "message": "/ at 97%", "detail": "/ at 97%"}],
    )
    assert "CRITICAL" in body
    assert "disk full" in body
    assert "/ at 97%" in body
