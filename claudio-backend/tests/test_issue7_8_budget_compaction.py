"""Acceptance tests for issues #7 and #8 (Multi-LLM follow-ups).

#7 -- sub-agent (delegate_task) cost is rolled into the run's budget totals,
      so the combined primary + worker spend is reflected in ``run_completed``
      usage and the ``max_budget_usd`` guard sees it.

#8 -- the compaction budget (the ``context_usage`` gauge and the Compactor's
      trigger window) is driven by the ``fast`` profile, not the primary's,
      while the circuit-breaker behaviour stays intact.
"""
from __future__ import annotations

import json
import time

import pytest

from claudio_core.compaction import Compactor, CompactionFailedError
from claudio_core.context import context_usage_payload
from claudio_core.db import Database
from claudio_core.metrics import Metrics
from claudio_core.session_store import SessionStore
from claudio_core.settings import AppConfig, resolve_profile

from claudio_backend.runs import manager as manager_mod
from claudio_backend.runs.manager import RunManager, RunRecord


# --------------------------------------------------------------------------
# Config helpers
# --------------------------------------------------------------------------

def _profiled_config(tmp_path):
    """A config whose fast profile has a much smaller window than the primary."""
    return AppConfig.model_validate(
        {
            "server": {"auth_token": "test-token"},
            "llm": {
                "model": "primary-model",
                "base_url": "http://primary/v1",
                "api_key": "primary-key",
                "price_input_per_mtok": 1.0,
                "price_output_per_mtok": 2.0,
            },
            "model_profiles": {
                "worker": {
                    "model": "worker-model",
                    "base_url": "http://worker/v1",
                    "api_key": "worker-key",
                    "context_window": 4096,
                    "price_input_per_mtok": 0.1,
                    "price_output_per_mtok": 0.2,
                },
                "primary-profile": {
                    "model": "named-primary",
                    "base_url": "http://named-primary/v1",
                    "api_key": "named-primary-key",
                    "context_window": 32768,
                    "price_input_per_mtok": 3.0,
                    "price_output_per_mtok": 4.0,
                },
            },
            "roles": {
                "primary": "primary-profile",
                "worker": "worker",
                "fast": "worker",
            },
            "agent": {
                "default_workspace_dir": str(tmp_path / "ws"),
                "context_window": 32768,
                "compaction_reserve": 512,
                "compaction_timeout_seconds": 300,
                "max_auto_continues": 10,
                "tools": {"enabled": ["delegate_task"]},
                "subagents": {
                    "coder": {"tools": ["read_file"], "max_turns": 4}
                },
            },
        }
    )


class _ScriptedEngine:
    """Engine stand-in that replays one scripted event list per attempt."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.attempts = 0

    async def stream(self, request):
        idx = min(self.attempts, len(self.scripts) - 1)
        self.attempts += 1
        for ev in self.scripts[idx]:
            yield ev

    async def get_conversation_items(self, session_id, workspace_id):
        return []

    async def replace_conversation(self, session_id, workspace_id, summary):
        pass

    async def cancel(self, run_id):
        pass


def _delegate_result(input_tokens, output_tokens, cost_usd, ok=True):
    return {
        "event": "tool_result",
        "data": {
            "name": "delegate_task",
            "output": json.dumps(
                {
                    "ok": ok,
                    "output": "worker done",
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": cost_usd,
                    },
                }
            ),
        },
    }


def _completed(input_tokens, output_tokens, cost_usd):
    return {
        "event": "run_completed",
        "data": {
            "final_output": "all done",
            "turns": 1,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
            },
        },
    }


async def _make(tmp_path, scripts, *, budget=0.0, todos=None):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    await db.create_session("s1", title="t", workspace_dir=str(tmp_path / "ws"))
    await db.create_run("r1", "s1")
    if todos is not None:
        await db.update_session("s1", todos=json.dumps(todos))
    config = _profiled_config(tmp_path)
    config.agent.max_budget_usd = budget
    compactor = Compactor(
        context_window=resolve_profile(config, "fast").context_window,
        reserve=config.agent.compaction_reserve,
    )
    engine = _ScriptedEngine(scripts)
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
    record.started_at = time.time()
    return db, engine, mgr, record


# --------------------------------------------------------------------------
# Issue #7 -- delegated cost rolled into run budget totals
# --------------------------------------------------------------------------

async def test_delegate_cost_reflected_in_run_completed_usage(tmp_path):
    # A run that delegates a task with non-zero worker tokens reflects the
    # combined primary + worker cost in its run_completed usage.
    scripts = [
        [
            {"event": "tool_call", "data": {"name": "delegate_task", "arguments": "{}"}},
            _delegate_result(1000, 200, 0.5),
            _completed(100, 50, 0.1),
        ]
    ]
    db, engine, mgr, record = await _make(tmp_path, scripts)
    try:
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)

        # Delegated spend accumulated on the record.
        assert record.delegated_input_tokens == 1000
        assert record.delegated_output_tokens == 200
        assert record.delegated_cost_usd == pytest.approx(0.5)

        # The published run_completed usage is the COMBINED total.
        completed = [
            e for _s, e in record.events if e.get("event") == "run_completed"
        ]
        assert len(completed) == 1
        usage = completed[0]["data"]["usage"]
        assert usage["input_tokens"] == 1100  # 100 primary + 1000 worker
        assert usage["output_tokens"] == 250   # 50 primary + 200 worker
        assert usage["cost_usd"] == pytest.approx(0.6)  # 0.1 + 0.5
    finally:
        await db.close()


async def test_combined_spend_trips_max_budget(tmp_path):
    # With an open todo (so the auto-continue path is reached) and a budget
    # that the worker's spend alone blows through, the run must stop rather
    # than auto-continue -- the guard sees the delegated cost.
    open_todo = [{"content": "still to do", "status": "pending"}]
    scripts = [
        [
            {"event": "tool_call", "data": {"name": "delegate_task", "arguments": "{}"}},
            _delegate_result(2000, 400, 1.5),  # worker spends $1.50
            _completed(100, 50, 0.1),
        ],
        [
            _completed(100, 50, 0.1),
        ],
    ]
    db, engine, mgr, record = await _make(
        tmp_path, scripts, budget=1.0, todos=open_todo
    )
    try:
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)

        # Combined spend (0.1 + 1.5 = 1.6) >= budget (1.0): the run stops
        # after the first turn instead of auto-continuing.
        assert record.cost_usd == pytest.approx(1.6)
        assert engine.attempts == 1
    finally:
        await db.close()


async def test_without_delegated_cost_run_would_have_continued(tmp_path):
    # Control: same budget, but the worker spend is tiny, so combined stays
    # under budget and the run auto-continues (proves the trip above is
    # driven by the delegated cost, not the budget being trivially low).
    open_todo = [{"content": "still to do", "status": "pending"}]
    scripts = [
        [
            {"event": "tool_call", "data": {"name": "delegate_task", "arguments": "{}"}},
            _delegate_result(10, 5, 0.001),
            _completed(100, 50, 0.1),
        ],
        [
            _completed(100, 50, 0.1),
        ],
    ]
    db, engine, mgr, record = await _make(
        tmp_path, scripts, budget=1.0, todos=open_todo
    )
    try:
        request = mgr._build_request(record, "do the thing", None)
        await mgr._run_with_recovery(record, request, 0.0)
        # Under budget -> the auto-continue path runs (more than one attempt).
        assert engine.attempts >= 2
    finally:
        await db.close()


# --------------------------------------------------------------------------
# Issue #8 -- compaction budget + summariser from the fast profile
# --------------------------------------------------------------------------

def test_context_usage_gauge_uses_fast_profile_window():
    config = _profiled_config(__import__("pathlib").Path("/tmp"))
    payload = context_usage_payload(1000, config, "model")
    # The gauge reports the fast profile's window, not the primary's 32768.
    assert payload["context_window"] == 4096
    assert payload["compact_threshold"] == 4096 - 512
    assert payload["usable_tokens"] == 4096 - 512


def test_compaction_triggers_against_fast_window():
    # A small fast window means compaction fires early, independent of the
    # primary's large window.
    fast_window = 4096
    reserve = 512
    compactor = Compactor(context_window=fast_window, reserve=reserve)
    threshold = fast_window - reserve  # 3584
    assert compactor.should_compact(threshold) is True
    assert compactor.should_compact(threshold - 1) is False


async def test_summariser_uses_fast_profile(tmp_path, monkeypatch):
    captured = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return "summary text"

    monkeypatch.setattr(manager_mod, "openai_chat_completion", fake_completion)
    mgr = RunManager.__new__(RunManager)
    mgr.config = _profiled_config(tmp_path)
    mgr.compactor = Compactor(context_window=4096, reserve=512)
    out = await mgr._build_summariser()("prompt")
    assert out == "summary text"
    assert captured["model"] == "worker-model"
    assert captured["base_url"] == "http://worker/v1"


async def test_circuit_breaker_trips_on_repeated_failures():
    compactor = Compactor(context_window=4096, reserve=512)

    async def boom(request):
        raise RuntimeError("summariser down")

    items = [
        {"role": "user", "content": "a" * 500},
        {"role": "assistant", "content": "b" * 500},
    ]
    # First two failures re-raise but do not disable; the third trips.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await compactor.summarize(items, boom, "s1")
    assert compactor.is_disabled("s1") is False
    with pytest.raises(CompactionFailedError):
        await compactor.summarize(items, boom, "s1")
    assert compactor.is_disabled("s1") is True
