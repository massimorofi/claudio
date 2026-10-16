"""Tests for the maintenance handler: safe actions run, irreversible actions
become suggestions, report-only skips mutations, verdict mapping, and the audit
retention sweep.
"""
from __future__ import annotations

import pytest

from claudio.settings import AppConfig
from claudio.db import Database, utcnow
from claudio.autonomous.maintenance import (
    build_actions,
    run_maintenance,
    check_unattended_upgrades,
)


def make_runner(disk_pct=50.0, unattended="enabled", fail_actions=None):
    """A fake command runner that answers the commands maintenance issues.

    ``fail_actions`` is a set of substrings; any elevated command containing one
    returns a non-zero exit code.
    """
    fail_actions = fail_actions or set()
    calls: list[list[str]] = []

    async def runner(cmd):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "df" in cmd:
            used = int(1000 * disk_pct / 100)
            avail = 1000 - used
            out = (
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                f"/dev/sda1 1000 {used} {avail} {int(disk_pct)}% /\n"
            )
            return (0, out, "")
        if "is-enabled" in cmd:
            return (0 if unattended == "enabled" else 1, unattended + "\n", "")
        for bad in fail_actions:
            if bad in joined:
                return (1, "", f"failed: {bad}")
        return (0, "ok", "")

    runner.calls = calls
    return runner


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def notify(self, verdict, summary, details=None):
        self.sent.append((verdict, summary))


def full_maintenance_cfg():
    cfg = AppConfig()
    m = cfg.autonomous.maintenance
    m.apt_update = True
    m.vacuum_logs = True
    m.temp_cleanup = True
    m.autoremove = True
    m.full_upgrade = True
    m.docker_prune = True
    return cfg


# ---- build_actions -----------------------------------------------------


def test_build_actions_respects_toggles():
    cfg = AppConfig()
    cfg.autonomous.maintenance.apt_update = True
    cfg.autonomous.maintenance.docker_prune = False
    names = {a.name for a in build_actions(cfg)}
    assert "apt_update" in names
    assert "docker_prune" not in names


def test_build_actions_safe_before_irreversible():
    cfg = full_maintenance_cfg()
    actions = build_actions(cfg)
    # All safe actions precede all irreversible ones.
    first_irreversible = next(i for i, a in enumerate(actions) if not a.safe)
    assert all(a.safe for a in actions[:first_irreversible])


# ---- safe actions execute ----------------------------------------------


async def test_safe_actions_execute(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = False
    runner = make_runner(disk_pct=50.0)
    report = await run_maintenance(cfg, db, FakeNotifier(), runner=runner)

    executed_names = {e["name"] for e in report.details["executed"]}
    assert executed_names == {"apt_update", "vacuum_logs", "temp_cleanup"}
    assert report.verdict == "ok"
    # The elevated commands were run with sudo -n.
    assert ["sudo", "-n", "/usr/bin/apt-get", "update"] in runner.calls
    await db.close()


# ---- irreversible -> suggestions ---------------------------------------


async def test_irreversible_create_suggestions(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = False
    runner = make_runner()
    report = await run_maintenance(cfg, db, FakeNotifier(), runner=runner)

    suggested = report.details["suggested"]
    assert {s["name"] for s in suggested} == {"autoremove", "full_upgrade", "docker_prune"}
    # Each suggestion is high-risk, non-reversible, with no linked issue.
    for s in suggested:
        got = await db.get_suggestion(s["suggestion_id"])
        assert got["risk"] == "high"
        assert got["reversible"] == 0
        assert got["issue_id"] is None
        assert got["command"].startswith("sudo ")
    await db.close()


# ---- report_only skips mutations ---------------------------------------


async def test_report_only_skips_safe_actions(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = True
    runner = make_runner()
    report = await run_maintenance(cfg, db, FakeNotifier(), runner=runner)

    # No safe action executed; all recorded as "would run".
    assert report.details["executed"] == []
    assert set(report.details["would_run"]) == {"apt_update", "vacuum_logs", "temp_cleanup"}
    # The runner was never asked to run an elevated mutation.
    sudo_calls = [c for c in runner.calls if c[:2] == ["sudo", "-n"]]
    assert sudo_calls == []
    # Suggestions are still created (a suggestion mutates nothing).
    assert len(report.details["suggested"]) == 3
    # The refusal was audited.
    audit = await db.list_audit(action="mutation_refused_report_only")
    assert len(audit) == 3
    await db.close()


# ---- verdict mapping ---------------------------------------------------


async def test_verdict_warning_on_failure(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = False
    # Make the apt update fail.
    runner = make_runner(disk_pct=50.0, fail_actions={"/usr/bin/apt-get update"})
    report = await run_maintenance(cfg, db, FakeNotifier(), runner=runner)
    assert report.verdict == "warning"
    assert any("apt_update" in f for f in report.details["failures"])
    await db.close()


async def test_verdict_critical_disk_full(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = False
    # Disk at 97% -> critical after cleanup.
    runner = make_runner(disk_pct=97.0)
    report = await run_maintenance(cfg, db, FakeNotifier(), runner=runner)
    assert report.verdict == "critical"
    assert "disk" in report.summary.lower()
    await db.close()


async def test_critical_notifies(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = False
    runner = make_runner(disk_pct=97.0)
    notifier = FakeNotifier()
    await run_maintenance(cfg, db, notifier, runner=runner)
    assert len(notifier.sent) == 1
    assert notifier.sent[0][0] == "critical"
    await db.close()


# ---- audit retention sweep ---------------------------------------------


async def test_audit_retention_sweep(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    # One ancient audit row.
    await db.conn.execute(
        "INSERT INTO claudio_audit (created_at, actor, action) VALUES (?, ?, ?)",
        ("2000-01-01T00:00:00+00:00", "operator", "ancient"),
    )
    await db.conn.commit()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = False
    cfg.autonomous.maintenance.audit_retention_days = 30
    runner = make_runner()
    report = await run_maintenance(cfg, db, FakeNotifier(), runner=runner)
    assert report.details["audit_removed"] >= 1
    remaining = await db.list_audit()
    assert all(a["action"] != "ancient" for a in remaining)
    await db.close()


# ---- unattended-upgrades check -----------------------------------------


async def test_unattended_check_reported(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    cfg = full_maintenance_cfg()
    cfg.autonomous.report_only = False
    runner = make_runner(unattended="disabled")
    report = await run_maintenance(cfg, db, FakeNotifier(), runner=runner)
    assert report.details["unattended_upgrades"]["enabled"] is False
    await db.close()


async def test_check_unattended_upgrades_direct():
    async def runner(cmd):
        return (0, "enabled\n", "")

    res = await check_unattended_upgrades(runner)
    assert res["checked"] is True
    assert res["enabled"] is True
