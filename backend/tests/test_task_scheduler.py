"""Tests for the cron task scheduler: next-fire computation, firing due items,
wake-on-change, and enabled gating.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from claudio.settings import AppConfig
from claudio.db import Database
from claudio.autonomous.task_scheduler import TaskScheduler, _cron_next


class FakeRunner:
    def __init__(self):
        self.runs = []

    async def run(self, key, trigger="scheduled"):
        self.runs.append(("task", key, trigger))

    async def run_builtin(self, kind, trigger="scheduled"):
        self.runs.append(("builtin", kind, trigger))


async def make_db(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    return db


# ---- cron next-fire ----------------------------------------------------


def test_cron_next_basic():
    # 3am daily, from noon -> 3am next day.
    frm = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    nxt = _cron_next("0 3 * * *", frm)
    assert nxt.day == 18
    assert nxt.hour == 3


def test_cron_next_every_minute():
    frm = datetime(2026, 10, 17, 12, 0, 30, tzinfo=timezone.utc)
    nxt = _cron_next("* * * * *", frm)
    assert nxt.minute == 1


# ---- tick fires due items ----------------------------------------------


async def test_tick_fires_due_task(tmp_path):
    db = await make_db(tmp_path)
    await db.create_task(key="due", name="D", prompt="x", schedule="* * * * *")
    cfg = AppConfig()
    cfg.autonomous.enabled = True
    runner = FakeRunner()
    sched = TaskScheduler(task_runner=runner, db=db, config=cfg)

    spawned = []
    sched._spawn = lambda item_id: spawned.append(item_id)  # type: ignore

    # Force the task's next-fire into the past so it is due.
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    sched._next_fire["task:due"] = past

    await sched._tick()
    assert "task:due" in spawned
    await db.close()


async def test_tick_does_not_fire_future_task(tmp_path):
    db = await make_db(tmp_path)
    await db.create_task(key="future", name="F", prompt="x", schedule="0 3 * * *")
    cfg = AppConfig()
    cfg.autonomous.enabled = True
    runner = FakeRunner()
    sched = TaskScheduler(task_runner=runner, db=db, config=cfg)

    spawned = []
    sched._spawn = lambda item_id: spawned.append(item_id)  # type: ignore

    # next-fire far in the future.
    sched._next_fire["task:future"] = datetime.now(timezone.utc) + timedelta(hours=2)
    await sched._tick()
    assert spawned == []
    await db.close()


async def test_tick_fires_builtin_maintenance_when_enabled(tmp_path):
    db = await make_db(tmp_path)
    cfg = AppConfig()
    cfg.autonomous.enabled = True
    cfg.autonomous.maintenance.enabled = True
    runner = FakeRunner()
    sched = TaskScheduler(task_runner=runner, db=db, config=cfg)

    spawned = []
    sched._spawn = lambda item_id: spawned.append(item_id)  # type: ignore
    sched._next_fire["builtin:maintenance"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    await sched._tick()
    assert "builtin:maintenance" in spawned
    await db.close()


async def test_tick_skips_builtin_maintenance_when_disabled(tmp_path):
    db = await make_db(tmp_path)
    cfg = AppConfig()
    cfg.autonomous.enabled = True
    cfg.autonomous.maintenance.enabled = False
    runner = FakeRunner()
    sched = TaskScheduler(task_runner=runner, db=db, config=cfg)

    spawned = []
    sched._spawn = lambda item_id: spawned.append(item_id)  # type: ignore
    # Even with a stale next-fire, a disabled maintenance item is dropped.
    sched._next_fire["builtin:maintenance"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    await sched._tick()
    assert "builtin:maintenance" not in spawned
    await db.close()


# ---- wake --------------------------------------------------------------


def test_wake_sets_event():
    sched = TaskScheduler(task_runner=FakeRunner(), db=None, config=AppConfig())
    assert not sched._wake.is_set()
    sched.wake()
    assert sched._wake.is_set()


async def test_wait_returns_true_on_wake():
    sched = TaskScheduler(task_runner=FakeRunner(), db=None, config=AppConfig())

    async def set_soon():
        await asyncio.sleep(0.05)
        sched.wake()

    asyncio.create_task(set_soon())
    woke = await sched._wait(5.0)
    assert woke is True
    # The event is cleared after being consumed.
    assert not sched._wake.is_set()


async def test_wait_returns_false_on_timeout():
    sched = TaskScheduler(task_runner=FakeRunner(), db=None, config=AppConfig())
    woke = await sched._wait(0.05)
    assert woke is False


# ---- enabled gating ----------------------------------------------------


async def test_disabled_scheduler_does_not_fire(tmp_path):
    db = await make_db(tmp_path)
    await db.create_task(key="x", name="X", prompt="x", schedule="* * * * *")
    cfg = AppConfig()
    cfg.autonomous.enabled = False  # master off
    runner = FakeRunner()
    sched = TaskScheduler(task_runner=runner, db=db, config=cfg)
    # The loop's enabled check short-circuits before any tick.
    assert sched._enabled() is False
    await db.close()
