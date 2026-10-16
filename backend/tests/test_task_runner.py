"""Tests for the task runner: built-in dispatch, run records, notify policy,
overlap guard, and tolerant report parsing.
"""
from __future__ import annotations

import asyncio
import pytest

from claudio.settings import AppConfig
from claudio.db import Database
from claudio.autonomous.report import TaskReport
from claudio.autonomous.task_runner import TaskRunner


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def notify(self, verdict, summary, details=None):
        self.sent.append((verdict, summary))


async def ok_runner(cmd):
    return (0, "ok", "")


async def make_db(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    return db


# ---- built-in dispatch -------------------------------------------------


async def test_run_builtin_maintenance_records_run(tmp_path):
    db = await make_db(tmp_path)
    cfg = AppConfig()
    runner = ok_runner
    tr = TaskRunner(config=cfg, db=db, notifier=FakeNotifier())
    report = await tr.run_builtin("maintenance", trigger="manual", runner=runner)
    assert report.verdict in ("ok", "warning", "critical")
    runs = await db.list_task_runs(task_key="maintenance")
    assert len(runs) == 1
    assert runs[0]["trigger"] == "manual"
    assert runs[0]["finished_at"] is not None
    await db.close()


async def test_run_builtin_unknown_raises(tmp_path):
    db = await make_db(tmp_path)
    tr = TaskRunner(config=AppConfig(), db=db, notifier=FakeNotifier())
    with pytest.raises(ValueError):
        await tr.run_builtin("bogus")
    await db.close()


async def test_run_builtin_overlap_skipped(tmp_path):
    db = await make_db(tmp_path)
    tr = TaskRunner(config=AppConfig(), db=db, notifier=FakeNotifier())
    # Pretend maintenance is already running.
    tr._running.add("maintenance")
    report = await tr.run_builtin("maintenance", runner=ok_runner)
    assert report.verdict == "skipped"
    # No new run row was created for the skipped call.
    runs = await db.list_task_runs(task_key="maintenance")
    assert len(runs) == 0
    await db.close()


# ---- user task: records + notify policy --------------------------------


async def test_run_user_task_records_and_notifies(tmp_path):
    db = await make_db(tmp_path)
    await db.create_task(
        key="nightly",
        name="Nightly",
        prompt="check things",
        schedule="0 3 * * *",
        notify_on=["warning", "critical"],
    )
    cfg = AppConfig()
    tr = TaskRunner(config=cfg, db=db, notifier=FakeNotifier())

    canned = TaskReport(verdict="warning", summary="found a thing", report="details here")

    async def fake_agent(task, eff):
        return canned

    tr._run_agent_task = fake_agent  # type: ignore
    notifier = tr.notifier
    report = await tr.run("nightly", trigger="manual")
    assert report.verdict == "warning"
    # task_run updated with verdict + finished_at.
    runs = await db.list_task_runs(task_key="nightly")
    assert runs[0]["verdict"] == "warning"
    assert runs[0]["finished_at"] is not None
    # task last_run_at + last_verdict updated.
    task = await db.get_task("nightly")
    assert task["last_verdict"] == "warning"
    assert task["last_run_at"] is not None
    # notified because warning is in notify_on.
    assert len(notifier.sent) == 1
    await db.close()


async def test_run_user_task_silent_when_not_in_notify_on(tmp_path):
    db = await make_db(tmp_path)
    await db.create_task(
        key="quiet",
        name="Quiet",
        prompt="x",
        schedule="0 3 * * *",
        notify_on=[],  # never notify
    )
    tr = TaskRunner(config=AppConfig(), db=db, notifier=FakeNotifier())

    async def fake_agent(task, eff):
        return TaskReport(verdict="warning", summary="bad")

    tr._run_agent_task = fake_agent  # type: ignore
    report = await tr.run("quiet", trigger="manual")
    assert report.verdict == "warning"
    assert len(tr.notifier.sent) == 0  # silent
    await db.close()


async def test_run_unknown_task_raises(tmp_path):
    db = await make_db(tmp_path)
    tr = TaskRunner(config=AppConfig(), db=db, notifier=FakeNotifier())
    with pytest.raises(ValueError):
        await tr.run("nope")
    await db.close()


async def test_run_overlap_skipped(tmp_path):
    db = await make_db(tmp_path)
    await db.create_task(key="busy", name="B", prompt="x", schedule="0 3 * * *")
    tr = TaskRunner(config=AppConfig(), db=db, notifier=FakeNotifier())
    tr._running.add("busy")
    report = await tr.run("busy")
    assert report.verdict == "skipped"
    await db.close()


# ---- tolerant report parsing -------------------------------------------


def test_coerce_report_valid_json():
    r = TaskRunner._coerce_report('{"verdict": "warning", "summary": "s", "report": "r", "details": {"a": 1}}')
    assert r.verdict == "warning"
    assert r.summary == "s"
    assert r.details == {"a": 1}


def test_coerce_report_fenced_json():
    r = TaskRunner._coerce_report('```json\n{"verdict": "critical", "summary": "boom"}\n```')
    assert r.verdict == "critical"
    assert r.summary == "boom"


def test_coerce_report_invalid_json_defaults_ok():
    r = TaskRunner._coerce_report("just some prose, no json")
    assert r.verdict == "ok"
    assert "just some prose" in r.report


def test_coerce_report_bad_verdict_coerced():
    r = TaskRunner._coerce_report('{"verdict": "bogus", "summary": "x"}')
    assert r.verdict == "ok"


def test_coerce_report_missing_fields():
    r = TaskRunner._coerce_report('{"verdict": "warning"}')
    assert r.verdict == "warning"
    assert r.summary != ""  # falls back to text slice
