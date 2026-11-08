"""Manual compaction (POST /sessions/{id}/compact) actually compacts.

Issue #12: the compact button reported success but left the context gauge
unchanged, because the no-active-run path returned the stored token count
verbatim instead of summarising and replacing the conversation. These tests
drive ``RunManager.request_compact`` directly with a fake engine and a real
database, so the behaviour is checked without booting the ASGI app or a
live model.
"""
from __future__ import annotations

import types

import pytest

from claudio_core.compaction import Compactor
from claudio_core.db import Database
from claudio_core.metrics import Metrics
from claudio_core.settings import AppConfig

from claudio_backend.errors import ApiError
from claudio_backend.runs import manager as manager_mod
from claudio_backend.runs.manager import RunManager


class _FakeEngine:
    """Engine stand-in that records conversation replacement."""

    def __init__(self, items):
        self._items = items
        self.replaced_with: list[str] = []

    async def get_conversation_items(self, session_id, workspace_id):
        return list(self._items)

    async def replace_conversation(self, session_id, workspace_id, summary):
        self.replaced_with.append(summary)

    async def cancel(self, run_id):
        pass


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


async def _make_manager(tmp_path, items):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    await db.create_session("s1", title="t", workspace_dir=str(tmp_path / "ws"))
    config = _config(tmp_path)
    compactor = Compactor(
        context_window=config.agent.context_window,
        reserve=config.agent.compaction_reserve,
    )
    engine = _FakeEngine(items)
    mgr = RunManager(
        config=config,
        db=db,
        session_store=None,
        metrics=Metrics(),
        compactor=compactor,
        engine=engine,
    )
    return db, engine, mgr


async def test_compact_replaces_conversation_and_drops_gauge(tmp_path, monkeypatch):
    # A long conversation, with a big stored context size to start.
    db = None
    try:
        db, engine, mgr = await _make_manager(tmp_path, _items(8))
        await db.update_session("s1", last_context_tokens=30000)

        summary = "PROGRESS:\nDONE: wrote app.py\nTODO: tests"
        monkeypatch.setattr(
            manager_mod,
            "openai_chat_completion",
            lambda **kw: _async(summary),
        )

        res = await mgr.request_compact("s1")

        assert res["ok"] is True
        assert res["compacted"] is True
        # The gauge dropped to the summary's size, not the old 30000.
        assert res["used_tokens"] == max(1, len(summary) // 4)
        assert res["used_tokens"] < 30000
        assert res["context_usage"]["source"] == "compacted"
        # The engine's store was actually rewritten with the summary.
        assert engine.replaced_with == [summary]
        # And the new size was persisted.
        row = await db.get_session("s1")
        assert row["last_context_tokens"] == res["used_tokens"]
    finally:
        if db is not None:
            await db.close()


async def test_compact_reports_nothing_to_compact(tmp_path):
    db = None
    try:
        db, engine, mgr = await _make_manager(tmp_path, _items(1))
        await db.update_session("s1", last_context_tokens=500)
        res = await mgr.request_compact("s1")
        assert res["ok"] is True
        assert res["compacted"] is False
        assert res["reason"] == "nothing_to_compact"
        # Gauge unchanged — nothing was claimed to have been reduced.
        assert res["used_tokens"] == 500
        assert engine.replaced_with == []
    finally:
        if db is not None:
            await db.close()


async def test_compact_failure_trips_and_persists_breaker(tmp_path, monkeypatch):
    db = None
    try:
        db, engine, mgr = await _make_manager(tmp_path, _items(8))
        await db.update_session("s1", last_context_tokens=30000)

        def _boom(**kw):
            raise RuntimeError("summariser down")

        monkeypatch.setattr(manager_mod, "openai_chat_completion", _boom)

        # CIRCUIT_BREAKER_THRESHOLD = 3 failures disable the session.
        for _ in range(3):
            res = await mgr.request_compact("s1")
            assert res["ok"] is False
            assert "Compaction failed" in res["error"]

        # Once tripped, the disabled flag is persisted so it survives restart.
        row = await db.get_session("s1")
        assert row["compaction_disabled"] == 1
        # The conversation was never replaced.
        assert engine.replaced_with == []
    finally:
        if db is not None:
            await db.close()


async def test_compact_refused_while_awaiting_approval(tmp_path):
    db = None
    try:
        db, engine, mgr = await _make_manager(tmp_path, _items(8))
        # Forge an active run paused on approval.
        from claudio_backend.runs.manager import RunRecord

        rec = RunRecord(
            run_id="r1",
            session_id="s1",
            workspace=tmp_path / "ws",
            workspace_id="w1",
            approval_mode="on_request",
        )
        rec.state = "awaiting_approval"
        mgr._runs["r1"] = rec

        with pytest.raises(ApiError) as ei:
            await mgr.request_compact("s1")
        assert ei.value.status == 409
    finally:
        if db is not None:
            await db.close()


async def _async(value):
    return value
