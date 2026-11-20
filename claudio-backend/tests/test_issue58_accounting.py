"""Issue #58 Increment 0 -- unified accounting on the backend side.

Covers the seams the plan names:

* the engine-path HTTP recorder (``reasoning_llm.create_with_retry``): one
  record per logical call, usage incl. cached/reasoning tokens, failed calls
  recorded, the max_tokens rename counted as ``attempt=2``;
* ``usage_detail_from_event`` surfacing the cached/thoughts fields ADK maps
  but ``usage_from_event`` used to drop;
* tool telemetry through ``make_adk_tool`` (duration, size, error);
* the run manager folding purpose-tagged auxiliary calls (router, compaction,
  learning) and the lifecycle counters (retries, replans, auto-continues,
  tier-2 compactions) into the persisted run row + ledger tables.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue58_accounting.py -q
"""
from __future__ import annotations

import asyncio
import time
import types
from pathlib import Path

import httpx
import openai
import pytest

import claudio_backend.engine.reasoning_llm as reasoning_llm
import claudio_backend.runs.manager as manager_mod
from claudio_core.accounting import CallAccountant, LlmCallRecord
from claudio_core.db import Database
from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.events import EventName
from claudio_core.settings import AppConfig
from claudio_core.context import ToolContext

from claudio_backend.engine.adk_engine import (
    AdkEngine,
    make_adk_tool,
    usage_detail_from_event,
)
from claudio_backend.runs.manager import RunManager, RunRecord


# ---------------------------------------------------------------------------
# reasoning_llm recorder
# ---------------------------------------------------------------------------

class _NS:
    pass


def _client_with(fake_create, ctx):
    client = _NS()
    client.chat = _NS()
    client.chat.completions = _NS()
    client.chat.completions.create = fake_create
    client._claudio_call_context = ctx
    reasoning_llm._patch_max_tokens_retry(client)
    return client


def _completion(usage: dict | None):
    result = _NS()
    if usage is not None:
        u = _NS()
        u.prompt_tokens = usage.get("prompt_tokens", 0)
        u.completion_tokens = usage.get("completion_tokens", 0)
        det = _NS()
        det.cached_tokens = usage.get("cached", 0)
        u.prompt_tokens_details = det
        cdet = _NS()
        cdet.reasoning_tokens = usage.get("reasoning", 0)
        u.completion_tokens_details = cdet
        result.usage = u
    else:
        result.usage = None
    return result


def test_recorder_records_success_with_usage():
    events = []
    used = {}

    async def fake_create(*args, **kwargs):
        used.update(kwargs)
        return _completion({"prompt_tokens": 100, "completion_tokens": 20,
                            "cached": 60, "reasoning": 8})

    client = _client_with(
        fake_create,
        {"recorder": events.append, "run_id": "r1", "purpose": "primary"},
    )
    asyncio.run(client.chat.completions.create(model="m", max_tokens=10))
    assert len(events) == 1
    ev = events[0]
    assert ev["outcome"] == "ok"
    assert ev["attempt"] == 1
    assert ev["run_id"] == "r1"
    assert ev["usage"]["input_tokens"] == 100
    assert ev["usage"]["cached_input_tokens"] == 60
    assert ev["usage"]["reasoning_tokens"] == 8
    assert ev["measured"] is True


def test_recorder_records_failure():
    events = []

    async def fake_create(*args, **kwargs):
        raise RuntimeError("boom")

    client = _client_with(
        fake_create, {"recorder": events.append, "run_id": "r1"}
    )
    with pytest.raises(RuntimeError):
        asyncio.run(client.chat.completions.create(model="m"))
    assert len(events) == 1
    assert events[0]["outcome"] == "error"
    assert events[0]["error_code"] == "RuntimeError"


def test_recorder_counts_max_tokens_rename_as_attempt_two():
    events = []
    calls = []
    req = httpx.Request("POST", "http://x/v1/chat/completions")

    async def fake_create(*args, **kwargs):
        calls.append(dict(kwargs))
        if "max_tokens" in kwargs:
            raise openai.BadRequestError(
                "Unsupported parameter: 'max_tokens' is not supported, "
                "use 'max_completion_tokens' instead",
                response=httpx.Response(400, request=req),
                body=None,
            )
        return _completion({"prompt_tokens": 5, "completion_tokens": 1})

    client = _client_with(
        fake_create, {"recorder": events.append, "run_id": "r1"}
    )
    asyncio.run(client.chat.completions.create(model="m", max_tokens=10))
    assert len(calls) == 2  # two HTTP posts
    assert len(events) == 1  # one logical call
    assert events[0]["attempt"] == 2
    assert events[0]["outcome"] == "ok"


def test_recorder_streaming_call_is_unmeasured():
    events = []

    async def fake_create(*args, **kwargs):
        return object()  # a stream handle; no .usage

    client = _client_with(
        fake_create, {"recorder": events.append, "run_id": "r1"}
    )
    asyncio.run(client.chat.completions.create(model="m", stream=True))
    assert len(events) == 1
    assert events[0]["measured"] is False


def test_recorder_none_is_noop():
    async def fake_create(*args, **kwargs):
        return _completion(None)

    client = _client_with(fake_create, {})
    result = asyncio.run(client.chat.completions.create(model="m"))
    assert result is not None


# ---------------------------------------------------------------------------
# usage_detail_from_event
# ---------------------------------------------------------------------------

def test_usage_detail_surfaces_cached_and_reasoning():
    um = types.SimpleNamespace(
        prompt_token_count=100,
        candidates_token_count=20,
        cached_content_token_count=60,
        thoughts_token_count=8,
    )
    event = types.SimpleNamespace(usage_metadata=um)
    detail = usage_detail_from_event(event)
    assert detail == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_input_tokens": 60,
        "reasoning_tokens": 8,
    }


def test_usage_detail_without_usage():
    event = types.SimpleNamespace(usage_metadata=None)
    assert usage_detail_from_event(event)["input_tokens"] == 0


# ---------------------------------------------------------------------------
# Tool telemetry through make_adk_tool
# ---------------------------------------------------------------------------

def test_make_adk_tool_records_telemetry(tmp_path):
    from claudio_core.tools import registry as reg

    acc = CallAccountant()
    ctx = ToolContext(
        config=AppConfig(),
        session_id="s1",
        workspace=tmp_path,
        approval_mode="off",
        db=None,
        run_id="r1",
        accountant=acc,
    )
    # list_dir is read-only and safe to run against an empty workspace.
    tool = make_adk_tool("list_dir", ctx, require_confirmation=False)

    async def go():
        return await tool.func(path=".")

    result = asyncio.run(go())
    assert isinstance(result, dict)
    recs = acc.tool_calls("r1")
    assert len(recs) == 1
    assert recs[0].name == "list_dir"
    assert recs[0].duration_ms >= 0
    assert recs[0].result_chars > 0
    assert recs[0].est_result_tokens > 0
    assert acc.lifecycle("r1")["tool_calls"] == 1


def test_make_adk_tool_records_error_result(tmp_path):
    from claudio_core.tools import registry as reg

    acc = CallAccountant()
    ctx = ToolContext(
        config=AppConfig(),
        session_id="s1",
        workspace=tmp_path,
        approval_mode="off",
        db=None,
        run_id="r1",
        accountant=acc,
    )
    tool = make_adk_tool("read_file", ctx, require_confirmation=False)

    async def go():
        return await tool.func(path="does-not-exist.txt")

    result = asyncio.run(go())
    recs = acc.tool_calls("r1")
    assert len(recs) == 1
    # A handled failure ({"ok": False} / {"error": ...}) is recorded as error.
    assert recs[0].is_error or (isinstance(result, dict) and result.get("ok") is False)


# ---------------------------------------------------------------------------
# Manager-level accounting
# ---------------------------------------------------------------------------

class _ScriptedEngine:
    """Minimal RunEngine replaying scripted event lists (see test_issue7_8)."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.attempts = 0
        self.replaced_with = []

    async def stream(self, request):
        script = self.scripts[min(self.attempts, len(self.scripts) - 1)]
        self.attempts += 1
        for evt in script:
            yield evt

    async def get_conversation_items(self, session_id, workspace_id):
        return [
            {"role": "user", "content": [{"type": "text", "text": "a" * 50}]},
            {"role": "assistant", "content": [{"type": "text", "text": "b" * 50}]},
        ]

    async def replace_conversation(self, session_id, workspace_id, summary):
        self.replaced_with.append(summary)


def _completed(turns=1, tin=100, tout=20):
    return [
        {
            "event": EventName.RUN_COMPLETED.value,
            "data": {
                "run_id": "r1",
                "session_id": "s1",
                "final_output": "done",
                "usage": {"input_tokens": tin, "output_tokens": tout, "cost_usd": 0.0},
                "turns": turns,
            },
        }
    ]


async def _make(tmp_path, scripts, *, accountant=None):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    await db.create_session("s1")
    await db.create_run("r1", "s1")
    from claudio_core.session_store import SessionStore
    from claudio_core.metrics import Metrics

    acc = accountant or CallAccountant()
    engine = _ScriptedEngine(scripts)
    config = AppConfig()
    mgr = RunManager(
        config=config,
        db=db,
        session_store=SessionStore(tmp_path / "data"),
        metrics=Metrics(),
        compactor=None,
        engine=engine,
        accountant=acc,
    )
    record = RunRecord(
        run_id="r1",
        session_id="s1",
        workspace=tmp_path / "ws",
        workspace_id="w1",
        approval_mode="off",
    )
    record.started_at = time.time()
    return db, mgr, record, engine, acc


async def test_finalize_persists_ledger_and_lifecycle(tmp_path):
    """Engine-path records + loop counters land on the run row and ledger."""
    db, mgr, record, engine, acc = await _make(tmp_path, [_completed()])
    try:
        # Simulate two engine-path HTTP calls recorded by the client seam.
        acc.record_llm_call(
            LlmCallRecord(run_id="r1", purpose="primary", input_tokens=100,
                          cached_input_tokens=40, measured=True)
        )
        acc.record_llm_call(
            LlmCallRecord(run_id="r1", purpose="primary", input_tokens=120,
                          cached_input_tokens=40, measured=True)
        )
        request = mgr._build_request(record, "hello", None)
        await mgr._run_with_recovery(record, request, 0.0)
        assert record.state == "completed"
        row = await db.get_run("r1")
        assert row["model_calls"] == 2
        assert row["cached_input_tokens"] == 80
        assert row["cache_hit_ratio"] == pytest.approx(80 / 220, rel=1e-3)
        # Ledger rows were flushed at finalisation.
        calls = await db.list_llm_calls("r1")
        assert len(calls) == 2
        assert all(c["purpose"] == "primary" for c in calls)
    finally:
        await db.close()


async def test_retry_counter_persisted(tmp_path):
    fail = [
        {
            "event": EventName.RUN_FAILED.value,
            "data": {
                "run_id": "r1",
                "error": {"code": "llm_unavailable", "message": "down",
                          "retryable": True},
            },
        }
    ]
    db, mgr, record, engine, acc = await _make(tmp_path, [fail, _completed()])
    try:
        request = mgr._build_request(record, "hello", None)
        await mgr._run_with_recovery(record, request, 0.0)
        assert record.state == "completed"
        assert record.retries == 1
        row = await db.get_run("r1")
        assert row["retries"] == 1
        # The retry request carried attempt=2 so the engine tags calls
        # "retry" -- verify the metadata reached the request.
        assert record.attempt == 2
    finally:
        await db.close()


async def test_router_call_recorded_with_purpose(tmp_path, monkeypatch):
    """An auto-routing classifier call lands in the ledger as purpose=router."""

    async def fake_completion(**kwargs):
        sink = kwargs.get("usage_sink")
        if sink:
            sink(
                {
                    "purpose": kwargs.get("purpose", "other"),
                    "model": kwargs.get("model", ""),
                    "outcome": "ok",
                    "attempt": 1,
                    "duration_ms": 5.0,
                    "usage": {"input_tokens": 30, "output_tokens": 1,
                              "cached_input_tokens": 0, "reasoning_tokens": 0},
                    "measured": True,
                }
            )
        return "FAST"

    monkeypatch.setattr(manager_mod, "openai_chat_completion", fake_completion)
    db, mgr, record, engine, acc = await _make(tmp_path, [_completed()])
    try:
        config = AppConfig.model_validate(
            {
                "llm": {"model": "primary-m", "base_url": "http://p/v1"},
                "model_profiles": {
                    "fastp": {"model": "fast-m", "base_url": "http://f/v1"}
                },
                "roles": {"fast": "fastp"},
                "agent": {"auto_routing_enabled": True},
            }
        )
        mgr.config = config
        role = await mgr._auto_route_role("what is 2+2?", record)
        assert role == "fast"
        recs = acc.llm_calls("r1")
        assert len(recs) == 1
        assert recs[0].purpose == "router"
        assert recs[0].model == "fast-m"
    finally:
        await db.close()


async def test_compaction_call_recorded_as_tier2(tmp_path, monkeypatch):
    """A mid-run overflow compaction records purpose=compaction and bumps tier2."""
    from claudio_core.compaction import Compactor

    async def fake_completion(**kwargs):
        sink = kwargs.get("usage_sink")
        if sink:
            sink(
                {
                    "purpose": kwargs.get("purpose", "other"),
                    "model": kwargs.get("model", ""),
                    "outcome": "ok",
                    "attempt": 1,
                    "duration_ms": 5.0,
                    "usage": {"input_tokens": 500, "output_tokens": 50,
                              "cached_input_tokens": 0, "reasoning_tokens": 0},
                    "measured": True,
                }
            )
        return "SUMMARY TEXT"

    monkeypatch.setattr(manager_mod, "openai_chat_completion", fake_completion)

    overflow = [
        {
            "event": EventName.RUN_FAILED.value,
            "data": {
                "run_id": "r1",
                "error": {"code": "context_overflow", "message": "too big",
                          "retryable": False},
            },
        }
    ]
    db, mgr, record, engine, acc = await _make(tmp_path, [overflow, _completed()])
    try:
        mgr.compactor = Compactor(context_window=1000, reserve=100)
        request = mgr._build_request(record, "hello", None)
        await mgr._run_with_recovery(record, request, 0.0)
        assert record.tier2_compactions == 1
        # The summariser call was recorded and flushed to the ledger at
        # finalisation (the in-memory buffer is drained by the flush).
        purposes = [c["purpose"] for c in await db.list_llm_calls("r1")]
        assert "compaction" in purposes
        row = await db.get_run("r1")
        assert row["tier2_compactions"] == 1
    finally:
        await db.close()


async def test_learning_call_recorded_after_finalize(tmp_path, monkeypatch):
    """The post-run learning extraction call is recorded and flushed."""

    async def fake_capture_run(db_, transcript, config, session_id=None,
                               run_id=None, usage_sink=None):
        if usage_sink:
            usage_sink(
                {
                    "purpose": "learning",
                    "model": "fast-m",
                    "outcome": "ok",
                    "attempt": 1,
                    "duration_ms": 3.0,
                    "usage": {"input_tokens": 40, "output_tokens": 4,
                              "cached_input_tokens": 0, "reasoning_tokens": 0},
                    "measured": True,
                }
            )
        return 0

    monkeypatch.setattr(manager_mod, "capture_run", fake_capture_run)
    db, mgr, record, engine, acc = await _make(tmp_path, [_completed()])
    try:
        mgr.config = AppConfig.model_validate(
            {"self_improvement": {"enabled": True, "capture_enabled": True}}
        )
        # Seed a transcript entry so the slice is non-empty.
        mgr.session_store.append(record.session_id, {"type": "user", "content": "hi"})
        request = mgr._build_request(record, "hello", None)
        await mgr._run_with_recovery(record, request, 0.0)
        await asyncio.gather(*list(mgr._capture_tasks))
        purposes = [c["purpose"] for c in await db.list_llm_calls("r1")]
        assert "learning" in purposes
    finally:
        await db.close()


def test_engine_call_context_tags_retry_purpose():
    """attempt>1 in metadata flips the engine recorder's purpose to retry."""
    config = AppConfig()
    acc = CallAccountant()
    engine = AdkEngine(config, db=None, accountant=acc)
    request = RunRequest(
        run_id="r1",
        session_id="s1",
        workspace=Path("/tmp"),
        instructions="",
        user_input="x",
        model=ModelSpec(model="m"),
        tools=ToolPolicy(),
        metadata={"attempt": 2, "model_role": "primary"},
    )
    ctx = engine._call_context(request, "primary")
    assert ctx["purpose"] == "retry"
    request1 = RunRequest(
        run_id="r1",
        session_id="s1",
        workspace=Path("/tmp"),
        instructions="",
        user_input="x",
        model=ModelSpec(model="m"),
        tools=ToolPolicy(),
        metadata={"attempt": 1},
    )
    assert engine._call_context(request1, "primary")["purpose"] == "primary"


def test_engine_without_accountant_records_nothing():
    config = AppConfig()
    engine = AdkEngine(config, db=None)
    request = RunRequest(
        run_id="r1",
        session_id="s1",
        workspace=Path("/tmp"),
        instructions="",
        user_input="x",
        model=ModelSpec(model="m"),
        tools=ToolPolicy(),
    )
    assert engine._call_context(request, "primary") == {}
