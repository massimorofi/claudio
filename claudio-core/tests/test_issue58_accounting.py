"""Issue #58 Increment 0 -- unified LLM-call accounting (core side).

Pins the contracts the performance baseline rests on:

* every auxiliary call through ``openai_chat_completion`` emits exactly one
  accounting payload -- on success *and* on failure -- carrying provider usage
  including cached and reasoning tokens;
* the ``max_tokens`` rename retry is ONE logical call with ``attempt=2``, not
  two records;
* :class:`CallAccountant` buffers per run, aggregates correctly (cache-hit
  ratio over measured calls only), and flushes to SQLite exactly once;
* the ledger tables and the new ``claudio_runs`` columns exist and round-trip.

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_issue58_accounting.py -q
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claudio_core import llm
from claudio_core.accounting import (
    CallAccountant,
    LlmCallRecord,
    ToolCallRecord,
    cache_hit_ratio,
    estimate_tokens,
)
from claudio_core.db import Database
from claudio_core.settings import AppConfig


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_estimate_tokens_uses_guard_heuristic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("x" * 24) == 10  # 24 / 2.4
    assert estimate_tokens("x" * 10) == 4   # 10/2.4 = 4.16 -> 4


def test_cache_hit_ratio_clamps_and_guards_zero():
    assert cache_hit_ratio(0, 0) == 0.0
    assert cache_hit_ratio(50, 100) == 0.5
    assert cache_hit_ratio(200, 100) == 1.0   # clamped
    assert cache_hit_ratio(-5, 100) == 0.0    # clamped


def test_llm_call_record_round_trips_to_dict():
    rec = LlmCallRecord(run_id="r1", purpose="router", cached_input_tokens=7)
    d = rec.as_dict()
    assert d["purpose"] == "router"
    assert d["cached_input_tokens"] == 7
    assert d["measured"] is True


# ---------------------------------------------------------------------------
# openai_chat_completion sink
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not json")


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.requests.append(json)
        status, body = self.responses.pop(0)
        return _Resp(status, body)


def _install(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda *a, **k: client)
    return client


_OK_BODY = {
    "choices": [{"message": {"content": "hi"}}],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 60},
        "completion_tokens_details": {"reasoning_tokens": 8},
    },
}


def test_success_emits_one_measured_payload(monkeypatch):
    events = []
    _install(monkeypatch, [(200, _OK_BODY)])
    out = asyncio.run(
        llm.openai_chat_completion(
            base_url="http://x/v1",
            model="m",
            messages=[{"role": "user", "content": "q"}],
            purpose="compaction",
            usage_sink=events.append,
        )
    )
    assert out == "hi"
    assert len(events) == 1
    ev = events[0]
    assert ev["outcome"] == "ok"
    assert ev["purpose"] == "compaction"
    assert ev["attempt"] == 1
    assert ev["measured"] is True
    assert ev["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_input_tokens": 60,
        "reasoning_tokens": 8,
    }


def test_failure_emits_error_payload(monkeypatch):
    events = []
    _install(monkeypatch, [(500, {"error": "boom"})])
    with pytest.raises(llm.LlmCallError):
        asyncio.run(
            llm.openai_chat_completion(
                base_url="http://x/v1",
                model="m",
                messages=[],
                purpose="router",
                usage_sink=events.append,
            )
        )
    assert len(events) == 1
    assert events[0]["outcome"] == "error"
    assert events[0]["error_code"] == "http_500"
    assert events[0]["usage"] is None
    assert events[0]["measured"] is False


def test_max_tokens_retry_is_one_logical_call_with_attempt_two(monkeypatch):
    events = []
    reject = {"error": {"param": "max_tokens", "message": "use max_completion_tokens"}}
    _install(monkeypatch, [(400, reject), (200, _OK_BODY)])
    asyncio.run(
        llm.openai_chat_completion(
            base_url="http://x/v1",
            model="m",
            messages=[],
            usage_sink=events.append,
        )
    )
    # Two HTTP posts, ONE accounting record -- the rename is an attempt, not a
    # second call. This is what keeps physical posts and logical calls apart.
    assert len(events) == 1
    assert events[0]["attempt"] == 2
    assert events[0]["outcome"] == "ok"


def test_missing_base_url_still_records(monkeypatch):
    events = []
    with pytest.raises(llm.LlmCallError):
        asyncio.run(
            llm.openai_chat_completion(
                base_url=None,
                model="m",
                messages=[],
                usage_sink=events.append,
            )
        )
    assert len(events) == 1
    assert events[0]["outcome"] == "error"
    assert events[0]["error_code"] == "no_base_url"


def test_broken_sink_never_breaks_the_call(monkeypatch):
    _install(monkeypatch, [(200, _OK_BODY)])

    def bad_sink(payload):
        raise RuntimeError("sink exploded")

    out = asyncio.run(
        llm.openai_chat_completion(
            base_url="http://x/v1", model="m", messages=[], usage_sink=bad_sink
        )
    )
    assert out == "hi"


def test_response_without_usage_records_unmeasured(monkeypatch):
    events = []
    body = {"choices": [{"message": {"content": "hi"}}]}
    _install(monkeypatch, [(200, body)])
    asyncio.run(
        llm.openai_chat_completion(
            base_url="http://x/v1", model="m", messages=[], usage_sink=events.append
        )
    )
    assert events[0]["measured"] is False
    assert events[0]["usage"] is None


# ---------------------------------------------------------------------------
# CallAccountant
# ---------------------------------------------------------------------------

def test_accountant_buffers_and_aggregates_per_run():
    acc = CallAccountant()
    acc.record_llm_call(
        LlmCallRecord(
            run_id="r1", purpose="primary", input_tokens=100,
            output_tokens=10, cached_input_tokens=50, reasoning_tokens=4,
        )
    )
    acc.record_llm_call(
        LlmCallRecord(
            run_id="r1", purpose="compaction", input_tokens=200,
            output_tokens=5, cached_input_tokens=0, measured=False,
        )
    )
    acc.record_llm_call(LlmCallRecord(run_id="r2", purpose="primary", input_tokens=7))
    totals = acc.usage_totals("r1")
    # The unmeasured compaction call contributes no tokens.
    assert totals["input_tokens"] == 100
    assert totals["cached_input_tokens"] == 50
    assert totals["cache_hit_ratio"] == 0.5
    assert totals["calls"] == 2
    assert acc.lifecycle("r1")["model_calls"] == 2


def test_accountant_lifecycle_bumps():
    acc = CallAccountant()
    acc.bump_lifecycle("r1", "retries")
    acc.bump_lifecycle("r1", "retries", 2)
    assert acc.lifecycle("r1")["retries"] == 3


def test_accountant_sink_factory_records():
    acc = CallAccountant()
    sink = acc.make_llm_sink(run_id="r9", purpose="learning", model="fast-m")
    sink(
        {
            "purpose": "learning",
            "model": "fast-m",
            "outcome": "ok",
            "attempt": 1,
            "duration_ms": 12.5,
            "usage": {"input_tokens": 30, "output_tokens": 3,
                      "cached_input_tokens": 0, "reasoning_tokens": 0},
            "measured": True,
        }
    )
    recs = acc.llm_calls("r9")
    assert len(recs) == 1
    assert recs[0].purpose == "learning"
    assert recs[0].input_tokens == 30


def test_accountant_feeds_metrics():
    from claudio_core.metrics import Metrics

    m = Metrics()
    acc = CallAccountant(metrics=m)
    acc.record_llm_call(
        LlmCallRecord(run_id="r", input_tokens=10, cached_input_tokens=5,
                      duration_ms=40.0)
    )
    acc.record_llm_call(LlmCallRecord(run_id="r", outcome="error", error_code="x"))
    snap = m.snapshot()
    assert snap["llm_calls_total"] == 2
    assert snap["llm_errors_total"] == 1
    assert snap["cached_input_tokens_total"] == 5
    assert snap["prompt_cache_hit_ratio"] == 0.5
    assert snap["latency_p50_ms"] > 0  # dead counter now fed


# ---------------------------------------------------------------------------
# Database ledger
# ---------------------------------------------------------------------------

def test_db_ledger_roundtrip_and_stats(tmp_path):
    async def scenario():
        db = Database(tmp_path / "t.db")
        await db.connect()
        try:
            await db.add_llm_calls(
                [
                    LlmCallRecord(
                        run_id="r1", purpose="primary", input_tokens=100,
                        cached_input_tokens=40, duration_ms=10.0,
                    ).as_dict(),
                    LlmCallRecord(
                        run_id="r1", purpose="router", input_tokens=50,
                        cached_input_tokens=0, outcome="error",
                        error_code="http_500",
                    ).as_dict(),
                ]
            )
            rows = await db.list_llm_calls("r1")
            assert len(rows) == 2
            assert rows[0]["purpose"] == "primary"
            stats = await db.llm_call_stats("r1")
            assert stats["calls"] == 2
            assert stats["errors"] == 1
            assert stats["input_tokens"] == 150
            assert stats["cached_input_tokens"] == 40
            assert stats["cache_hit_ratio"] == round(40 / 150, 4)
            assert stats["by_purpose"] == {"primary": 1, "router": 1}
            # Tool telemetry round-trips too.
            await db.add_tool_calls(
                [ToolCallRecord(run_id="r1", name="read_file", duration_ms=3.0,
                                result_chars=240, est_result_tokens=100).as_dict()]
            )
            tools = await db.list_tool_calls("r1")
            assert tools[0]["name"] == "read_file"
            assert tools[0]["est_result_tokens"] == 100
        finally:
            await db.close()

    asyncio.run(scenario())


def test_flush_writes_buffer_and_clears(tmp_path):
    async def scenario():
        db = Database(tmp_path / "t.db")
        await db.connect()
        try:
            acc = CallAccountant()
            acc.record_llm_call(LlmCallRecord(run_id="r1", input_tokens=5))
            acc.record_tool_call(ToolCallRecord(run_id="r1", name="glob"))
            n = await acc.flush(db, "r1")
            assert n == 1
            assert await db.list_llm_calls("r1")
            assert await db.list_tool_calls("r1")
            # Second flush writes nothing new.
            assert await acc.flush(db, "r1") == 0
            assert len(await db.list_llm_calls("r1")) == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_run_row_accepts_accounting_columns(tmp_path):
    """The new lifecycle/cache columns exist and update_run writes them."""

    async def scenario():
        db = Database(tmp_path / "t.db")
        await db.connect()
        try:
            await db.create_session("s1")
            await db.create_run("r1", "s1")
            await db.update_run(
                "r1",
                state="completed",
                model_calls=4,
                retries=1,
                replans=2,
                auto_continues=3,
                tier1_compactions=0,
                tier2_compactions=1,
                cached_input_tokens=99,
                reasoning_tokens=7,
                tool_calls=12,
                cache_hit_ratio=0.25,
            )
            row = await db.get_run("r1")
            assert row["model_calls"] == 4
            assert row["retries"] == 1
            assert row["replans"] == 2
            assert row["auto_continues"] == 3
            assert row["tier2_compactions"] == 1
            assert row["cached_input_tokens"] == 99
            assert row["reasoning_tokens"] == 7
            assert row["tool_calls"] == 12
            assert row["cache_hit_ratio"] == 0.25
        finally:
            await db.close()

    asyncio.run(scenario())


def test_context_usage_payload_carries_cache_fields():
    from claudio_core.context import context_usage_payload

    payload = context_usage_payload(
        1000, AppConfig(), "model", cached_input_tokens=400, cache_hit_ratio=0.4
    )
    assert payload["cached_input_tokens"] == 400
    assert payload["cache_hit_ratio"] == 0.4
    # Defaults keep old call sites unchanged.
    plain = context_usage_payload(1000, AppConfig())
    assert plain["cached_input_tokens"] == 0
    assert plain["cache_hit_ratio"] == 0.0
