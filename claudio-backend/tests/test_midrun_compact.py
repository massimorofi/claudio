"""Mid-run compaction: overflow recovery actually compacts, and manual
requests land at run boundaries.

Issue #14: the ``context_overflow`` recovery published a ``run_compacted``
event and restarted from the continue prompt without ever summarising or
replacing the stored conversation, so the restart re-sent the same bloated
history and overflowed again. These tests drive ``RunManager``'s recovery
loop with a scripted engine and assert the conversation is actually
replaced -- and that a manual request made while a run is in flight is
consumed at the next boundary instead of being silently dropped.
"""
from __future__ import annotations

import pytest

from claudio_core.compaction import Compactor
from claudio_core.db import Database
from claudio_core.metrics import Metrics
from claudio_core.session_store import SessionStore
from claudio_core.settings import AppConfig

from claudio_backend.runs import manager as manager_mod
from claudio_backend.runs.manager import (
    MAX_MID_RUN_COMPACTIONS,
    RunManager,
    RunRecord,
)


class _ScriptedEngine:
    """Engine stand-in that replays one scripted event list per attempt."""

    def __init__(self, scripts, items=None):
        self.scripts = scripts
        self.items = items or []
        self.attempts = 0
        self.replaced_with: list[str] = []

    async def stream(self, request):
        idx = min(self.attempts, len(self.scripts) - 1)
        self.attempts += 1
        for ev in self.scripts[idx]:
            yield ev

    async def get_conversation_items(self, session_id, workspace_id):
        return list(self.items)

    async def replace_conversation(self, session_id, workspace_id, summary):
        self.replaced_with.append(summary)

    async def cancel(self, run_id):
        pass


OVERFLOW = {
    "event": "run_failed",
    "data": {
        "error": {
            "code": "context_overflow",
            "message": "input too long",
            "retryable": False,
        }
    },
}

COMPLETED = {
    "event": "run_completed",
    "data": {
        "final_output": "all done",
        "turns": 1,
        "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0},
    },
}

SUMMARY = "PROGRESS:\nDONE: wrote app.py\nTODO: tests"


def _config(tmp_path):
    return AppConfig(
        server={"auth_token": "test-token"},
        agent={
            "default_workspace_dir": str(tmp_path / "ws"),
            "context_window": 32768,
            "compaction_reserve": 8192,
            "compaction_timeout_seconds": 300,
        },
        llm={
            "model": "test-model",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "local",
        },
    )


def _items(n):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 200}
        for i in range(n)
    ]


async def _make(tmp_path, scripts, items=None):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    await db.create_session("s1", title="t", workspace_dir=str(tmp_path / "ws"))
    await db.create_run("r1", "s1")
    config = _config(tmp_path)
    compactor = Compactor(
        context_window=config.agent.context_window,
        reserve=config.agent.compaction_reserve,
    )
    engine = _ScriptedEngine(scripts, items)
    store = SessionStore(tmp_path / "data")
    mgr = RunManager(
        config=config,
        db=db,
        session_store=store,
        metrics=Metrics(),
        compactor=compactor,
        engine=engine,
    )
    record = RunRecord(
        run_id="r1",
        session_id="s1",
        workspace=tmp_path / "ws",
        workspace_id="w1",
        approval_mode="on_request",
    )
    import time

    record.started_at = time.time()
    return db, engine, mgr, record


def _events(record):
    return [evt for _seq, evt in record.events]


def _compacted_events(record):
    return [e for e in _events(record) if e.get("event") == "run_compacted"]


async def test_overflow_actually_compacts_before_restarting(tmp_path, monkeypatch):
    # Attempt 1 overflows; attempt 2 completes. The overflow must cause a real
    # summarise + replace, not the old cosmetic restart.
    monkeypatch.setattr(
        manager_mod, "openai_chat_completion", lambda **kw: _async(SUMMARY)
    )
    db, engine, mgr, record = await _make(
        tmp_path, [[OVERFLOW], [COMPLETED]], items=_items(8)
    )
    try:
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)

        # The conversation was really replaced with the summary.
        assert engine.replaced_with == [SUMMARY]
        assert record.mid_run_compactions == 1
        assert record.last_context_tokens == max(1, len(SUMMARY) // 4)
        # The compaction was announced with the overflow reason.
        compacted = _compacted_events(record)
        assert len(compacted) == 1
        assert compacted[0]["data"]["reason"] == "overflow"
        # And the new size was persisted so a reload shows the drop.
        row = await db.get_session("s1")
        assert row["last_context_tokens"] == max(1, len(SUMMARY) // 4)
        # The run reached completion on the second attempt.
        assert record.state == "completed"
    finally:
        await db.close()


async def test_overflow_with_failing_summariser_fails_run(tmp_path, monkeypatch):
    # If the summariser itself fails, restarting would just overflow again:
    # the run must stop with a clear failure, not burn five blind restarts.
    def _boom(**kw):
        raise RuntimeError("summariser down")

    monkeypatch.setattr(manager_mod, "openai_chat_completion", _boom)
    db, engine, mgr, record = await _make(tmp_path, [[OVERFLOW]], items=_items(8))
    try:
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)
        assert record.state == "failed"
        assert engine.replaced_with == []
        # Only one attempt was made -- no blind restarts.
        assert engine.attempts == 1
    finally:
        await db.close()


async def test_overflow_beyond_cap_fails(tmp_path, monkeypatch):
    # Every attempt overflows and every compaction succeeds; once the cap is
    # reached the next overflow fails the run instead of looping forever.
    monkeypatch.setattr(
        manager_mod, "openai_chat_completion", lambda **kw: _async(SUMMARY)
    )
    db, engine, mgr, record = await _make(tmp_path, [[OVERFLOW]], items=_items(8))
    try:
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)
        assert record.state == "failed"
        assert record.mid_run_compactions == MAX_MID_RUN_COMPACTIONS
        assert len(engine.replaced_with) == MAX_MID_RUN_COMPACTIONS
    finally:
        await db.close()


async def test_manual_request_consumed_at_boundary(tmp_path, monkeypatch):
    # A compaction requested while the run is in flight is applied at the
    # next boundary instead of being silently dropped.
    monkeypatch.setattr(
        manager_mod, "openai_chat_completion", lambda **kw: _async(SUMMARY)
    )
    db, engine, mgr, record = await _make(tmp_path, [[COMPLETED]], items=_items(8))
    try:
        record.compact_requested.set()
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)
        assert engine.replaced_with == [SUMMARY]
        assert not record.compact_requested.is_set()
        compacted = _compacted_events(record)
        assert len(compacted) == 1
        assert compacted[0]["data"]["reason"] == "manual"
        assert record.state == "completed"
    finally:
        await db.close()


async def test_proactive_compaction_at_threshold(tmp_path, monkeypatch):
    # Last turn's reported usage crossed window - reserve: the next attempt is
    # preceded by a compaction even without an overflow or a manual request.
    monkeypatch.setattr(
        manager_mod, "openai_chat_completion", lambda **kw: _async(SUMMARY)
    )
    db, engine, mgr, record = await _make(tmp_path, [[COMPLETED]], items=_items(8))
    try:
        # window 32768 - reserve 8192 = compact at 24576.
        record.last_context_tokens = 25000
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)
        assert engine.replaced_with == [SUMMARY]
        compacted = _compacted_events(record)
        assert len(compacted) == 1
        assert compacted[0]["data"]["reason"] == "proactive"
        # After compaction the gauge dropped below the threshold.
        assert record.last_context_tokens < 24576
    finally:
        await db.close()


async def test_request_compact_defers_when_run_starts_during_summariser(
    tmp_path, monkeypatch
):
    # The race: no run at entry, but one starts while the summariser is in
    # flight. The replace must be skipped and the request handed to the run.
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    await db.create_session("s1", title="t", workspace_dir=str(tmp_path / "ws"))
    config = _config(tmp_path)
    compactor = Compactor(
        context_window=config.agent.context_window,
        reserve=config.agent.compaction_reserve,
    )
    engine = _ScriptedEngine([[COMPLETED]], items=_items(8))
    mgr = RunManager(
        config=config,
        db=db,
        session_store=SessionStore(tmp_path / "data"),
        metrics=Metrics(),
        compactor=compactor,
        engine=engine,
    )

    async def _summarise_spawn_a_run(*args, **kwargs):
        # Simulate the user sending a chat message while we were summarising.
        racer = RunRecord(
            run_id="racer",
            session_id="s1",
            workspace=tmp_path / "ws",
            workspace_id="w1",
            approval_mode="on_request",
        )
        mgr._runs["racer"] = racer
        return SUMMARY

    monkeypatch.setattr(manager_mod, "openai_chat_completion", _summarise_spawn_a_run)
    try:
        res = await mgr.request_compact("s1")
        assert res["ok"] is True
        assert res.get("requested") is True
        assert res.get("reason") == "run_started_during_compact"
        # The live run's store was NOT rewritten out from under it.
        assert engine.replaced_with == []
        # The request was handed to the run for its next boundary.
        assert mgr._runs["racer"].compact_requested.is_set()
    finally:
        await db.close()


async def _async(value):
    return value
