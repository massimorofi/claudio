"""Per-run-slice capture.

Each transcript entry must be handed to the learning extractor exactly once.
The earlier behaviour read the *whole* cumulative transcript after every run,
so a single fixed incident was re-extracted on every later run and its
``recurrence_count`` climbed on phantom repeats -- enough to blow past the
promotion threshold for a bug that was already resolved.

These tests drive ``RunManager._schedule_capture`` directly with a real
Database + SessionStore and a stubbed extractor, so the watermark bookkeeping
is checked without an LLM.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_capture_slice.py -v
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from claudio_core.db import Database
from claudio_core.session_store import SessionStore
from claudio_core.settings import AppConfig

from claudio_backend.runs import manager as manager_mod
from claudio_backend.runs.manager import RunManager, RunRecord


@pytest_asyncio.fixture
async def harness(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    store = SessionStore(tmp_path / "sessions")
    cfg = AppConfig()
    cfg.self_improvement.enabled = True
    cfg.self_improvement.capture_enabled = True
    rm = RunManager(cfg, db, store, metrics=None, compactor=None, engine=None)
    yield db, store, rm
    await db.close()


async def _drain(rm: RunManager) -> None:
    """Await any capture tasks the scheduler spawned (they self-discard)."""
    tasks = list(rm._capture_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_capture_slices_by_watermark(harness, monkeypatch):
    db, store, rm = harness
    captured: list[list] = []

    async def fake_capture_run(db_, transcript, config, session_id=None, run_id=None):
        captured.append(list(transcript))
        return 0

    monkeypatch.setattr(manager_mod, "capture_run", fake_capture_run)

    sid = "sess-1"
    await db.create_session(sid, title="t")
    rec = RunRecord(run_id="r1", session_id=sid)

    # Run 1: three new entries -> all three captured, watermark -> 3.
    for i in range(3):
        store.append(sid, {"type": "user", "content": f"m{i}"})
    await rm._schedule_capture(rec)
    await _drain(rm)
    assert len(captured) == 1
    assert [e["content"] for e in captured[0]] == ["m0", "m1", "m2"]
    assert (await db.get_session(sid))["learning_captured_entries"] == 3

    # Run 2: two more entries -> ONLY the two new ones are captured, never the
    # three already seen. This is the whole point of the fix.
    for i in range(2):
        store.append(sid, {"type": "user", "content": f"n{i}"})
    await rm._schedule_capture(rec)
    await _drain(rm)
    assert len(captured) == 2
    assert [e["content"] for e in captured[1]] == ["n0", "n1"]
    assert (await db.get_session(sid))["learning_captured_entries"] == 5

    # Run 3: nothing new appended -> capture is skipped entirely.
    await rm._schedule_capture(rec)
    await _drain(rm)
    assert len(captured) == 2  # no third call


@pytest.mark.asyncio
async def test_stale_watermark_above_transcript_is_clamped(harness, monkeypatch):
    """If the transcript was truncated below the stored watermark, the clamp
    must not crash or skip future entries -- it just yields an empty slice."""
    db, store, rm = harness
    captured: list[list] = []

    async def fake_capture_run(db_, transcript, config, session_id=None, run_id=None):
        captured.append(list(transcript))
        return 0

    monkeypatch.setattr(manager_mod, "capture_run", fake_capture_run)

    sid = "sess-clamp"
    await db.create_session(sid, title="t")
    # Pretend a previous capture recorded 10 entries, but the transcript only
    # has 2 (e.g. it was cleared and only partially rebuilt).
    await db.update_session(sid, learning_captured_entries=10)
    store.append(sid, {"type": "user", "content": "a"})
    store.append(sid, {"type": "user", "content": "b"})

    await rm._schedule_capture(RunRecord(run_id="r", session_id=sid))
    await _drain(rm)
    # start = min(10, 2) = 2 -> empty slice -> no capture, no crash.
    assert captured == []
    # Watermark is corrected down to the real length so the next run resumes
    # from the right place.
    assert (await db.get_session(sid))["learning_captured_entries"] == 2


@pytest.mark.asyncio
async def test_capture_disabled_skips(harness, monkeypatch):
    db, store, rm = harness
    called: list[int] = []

    async def fake_capture_run(*a, **k):
        called.append(1)
        return 0

    monkeypatch.setattr(manager_mod, "capture_run", fake_capture_run)
    rm.config.self_improvement.enabled = False

    sid = "sess-off"
    await db.create_session(sid, title="t")
    store.append(sid, {"type": "user", "content": "x"})
    await rm._schedule_capture(RunRecord(run_id="r", session_id=sid))
    await _drain(rm)
    assert called == []
