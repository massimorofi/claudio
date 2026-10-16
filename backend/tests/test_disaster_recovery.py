"""Tests for the boot-time disaster recovery handler.

Named ``test_disaster_recovery`` (not ``test_recovery``) because the latter is
already used by the run-recovery tests.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from claudio.settings import AppConfig
from claudio.db import Database
from claudio.autonomous.recovery import (
    run_recovery,
    _normalize_unit,
    _unit_in_allowlist,
)


class FakeRunner:
    """Answers every command the recovery handler issues, with mutable state so
    the post-restart recheck can differ from the first read."""

    def __init__(
        self,
        failed_first=None,
        failed_after=None,
        active=None,
        journal="",
        disk_pct=50.0,
        fail_restart=None,
    ):
        self.failed_first = failed_first or []
        self.failed_after = failed_after if failed_after is not None else []
        self.active = active or set()
        self.journal = journal
        self.disk_pct = disk_pct
        self.fail_restart = fail_restart or set()
        self.failed_reads = 0
        self.restart_calls: list[str] = []
        self.calls: list[list[str]] = []

    async def __call__(self, cmd):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if cmd[:2] == ["systemctl", "--failed"]:
            self.failed_reads += 1
            units = self.failed_first if self.failed_reads == 1 else self.failed_after
            out = "\n".join(f"{u} loaded failed failed" for u in units)
            return (0, (out + "\n") if out else "", "")
        if cmd[:2] == ["systemctl", "is-active"]:
            svc = cmd[2]
            state = "active" if svc in self.active else "inactive"
            return (0 if state == "active" else 3, state + "\n", "")
        if cmd[:4] == ["sudo", "-n", "/usr/bin/systemctl", "restart"]:
            unit = cmd[4]
            self.restart_calls.append(unit)
            if unit in self.fail_restart:
                return (1, "", f"restart failed: {unit}")
            return (0, "", "")
        if "df" in cmd:
            used = int(1000 * self.disk_pct / 100)
            avail = 1000 - used
            out = (
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                f"/dev/sda1 1000 {used} {avail} {int(self.disk_pct)}% /\n"
            )
            return (0, out, "")
        if "vacuum" in joined:
            return (0, "", "")
        if "cleanup_tmp.sh" in joined:
            return (0, "", "")
        if "journalctl" in cmd:
            return (0, self.journal, "")
        return (0, "", "")


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def notify(self, verdict, summary, details=None):
        self.sent.append((verdict, summary))


def make_cfg(tmp_path, *, backups_ok=True, restart_allowlist=None, expected=None):
    cfg = AppConfig()
    if backups_ok:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(exist_ok=True)
        f = backup_dir / "backup.tar"
        f.write_bytes(b"x")
        # touch to "now" so freshness passes
        now = datetime.now(timezone.utc).timestamp()
        os.utime(f, (now, now))
        cfg.autonomous.health_check.backup_path = str(backup_dir)
        cfg.autonomous.health_check.backup_max_age_hours = 26.0
    else:
        cfg.autonomous.health_check.backup_path = str(tmp_path / "nope")
    cfg.autonomous.recovery.restart_allowlist = restart_allowlist or []
    cfg.autonomous.recovery.expected_services = expected or []
    return cfg


# ---- unit normalization ------------------------------------------------


def test_normalize_unit_strips_service_suffix():
    assert _normalize_unit("nginx.service") == "nginx"
    assert _normalize_unit("nginx") == "nginx"
    assert _normalize_unit("docker.socket") == "docker.socket"


def test_unit_in_allowlist_matches_bare_and_full():
    assert _unit_in_allowlist("nginx.service", ["nginx"]) is True
    assert _unit_in_allowlist("nginx", ["nginx.service"]) is True
    assert _unit_in_allowlist("redis.service", ["nginx"]) is False


# ---- failed unit restart -----------------------------------------------


async def test_failed_unit_restarted_once(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path, restart_allowlist=["nginx"])
    cfg.autonomous.report_only = False
    # nginx fails, then is fine after restart.
    runner = FakeRunner(failed_first=["nginx.service"], failed_after=[])
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert "nginx" in runner.restart_calls
    assert report.verdict == "ok"
    await db.close()


async def test_failed_unit_not_in_allowlist_not_restarted(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path, restart_allowlist=["nginx"])
    cfg.autonomous.report_only = False
    runner = FakeRunner(failed_first=["redis.service"], failed_after=["redis.service"])
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert runner.restart_calls == []
    assert report.verdict == "warning"
    assert any("redis" in w for w in report.details["warnings"])
    await db.close()


# ---- per-boot guard ----------------------------------------------------


async def test_per_boot_guard_prevents_re_restart(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path, restart_allowlist=["nginx"])
    cfg.autonomous.report_only = False
    # nginx stays failed across both runs.
    runner = FakeRunner(failed_first=["nginx.service"], failed_after=["nginx.service"])
    await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert runner.restart_calls == ["nginx"]
    # Second run: already attempted this boot -> no new restart.
    await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert runner.restart_calls == ["nginx"]  # still just the one
    await db.close()


# ---- disk full path ----------------------------------------------------


async def test_disk_full_triggers_cleanup(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path)
    cfg.autonomous.report_only = False
    # Disk critical on first read, still critical after cleanup -> critical.
    runner = FakeRunner(disk_pct=97.0)
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    joined = " ".join(" ".join(c) for c in runner.calls)
    assert "--vacuum-size" in joined
    assert "cleanup_tmp.sh" in joined
    assert report.verdict == "critical"
    await db.close()


# ---- backup missing ----------------------------------------------------


async def test_backup_missing_is_critical(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path, backups_ok=False)
    cfg.autonomous.report_only = False
    runner = FakeRunner()
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert report.verdict == "critical"
    assert any("backup" in c.lower() for c in report.details["critical"])
    await db.close()


# ---- report_only skips restarts ----------------------------------------


async def test_report_only_skips_restarts(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path, restart_allowlist=["nginx"], expected=["postgresql"])
    cfg.autonomous.report_only = True
    runner = FakeRunner(failed_first=["nginx.service"], failed_after=["nginx.service"], active=set())
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    # No elevated restart commands at all.
    sudo_restart = [c for c in runner.calls if c[:4] == ["sudo", "-n", "/usr/bin/systemctl", "restart"]]
    assert sudo_restart == []
    # But the would-restart actions are recorded.
    actions = {a["action"] for a in report.details["actions"]}
    assert "would_restart_unit" in actions
    assert "would_restart_service" in actions
    await db.close()


# ---- expected service down ---------------------------------------------


async def test_expected_service_restarted(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path, expected=["postgresql"])
    cfg.autonomous.report_only = False
    # postgresql is active (so the is-active check passes after restart).
    runner = FakeRunner(active={"postgresql"})
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert report.verdict == "ok"
    await db.close()


async def test_expected_service_still_down_is_critical(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path, expected=["postgresql"])
    cfg.autonomous.report_only = False
    # postgresql never comes up.
    runner = FakeRunner(active=set())
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert report.verdict == "critical"
    assert any("postgresql" in c for c in report.details["critical"])
    await db.close()


# ---- kernel filesystem errors ------------------------------------------


async def test_kernel_fs_errors_critical(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path)
    cfg.autonomous.report_only = False
    runner = FakeRunner(journal="Oct 17 EXT4-fs error (device sda1): ext4_find_entry\n")
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert report.verdict == "critical"
    assert any("filesystem error" in c.lower() for c in report.details["critical"])
    await db.close()


async def test_all_clear(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = make_cfg(tmp_path)
    cfg.autonomous.report_only = False
    runner = FakeRunner()
    report = await run_recovery(cfg, db, FakeNotifier(), runner=runner)
    assert report.verdict == "ok"
    await db.close()
