"""Issue #58 Increment 0/0b -- telemetry aggregates and the concurrency overlap ratio.

Increment 0 built the ledger (``claudio_core.accounting``). Increment 0b is the
part that reads it back: the plan's acceptance signal for Increment 1's bounded
parallel reads is that a read batch reports an **overlap ratio greater than 1**
-- i.e. the calls genuinely shared wall-clock instead of queueing behind each
other. A serial sequence sits at ~1.0 by construction, so the metric cannot be
gamed by making calls faster.

These tests pin:

* ``overlap_ratio`` maths (serial ~1, parallel > 1, empty/zero-span 0);
* that :func:`run_tool_batch` tags its records with a shared ``concurrency_group``
  so the ratio can be measured per batch, and that mutating calls stay outside
  that group;
* the ledger aggregates the plan asks for: cache-hit ratio, tool payload weight,
  and a flush that carries ``concurrency_group`` through to storage.

Run:  cd claudio-backend && PYTHONPATH=src:../claudio-core/src \\
        python -m pytest tests/test_issue58_telemetry.py -q
"""
from __future__ import annotations

import asyncio

from claudio_core.accounting import (
    CallAccountant,
    LlmCallRecord,
    ToolCallRecord,
    cache_hit_ratio,
    estimate_tokens,
    overlap_ratio,
)
from claudio_core.context import RunConcurrency, ToolContext
from claudio_core.settings import AppConfig
from claudio_core.tool_batch import run_tool_batch
from claudio_core.tools.registry import TOOL_META, ToolMeta


def _ctx(tmp_path, accountant, bound=4):
    return ToolContext(
        config=AppConfig(),
        session_id="s1",
        workspace=tmp_path,
        approval_mode="off",
        db=None,
        run_id="r1",
        accountant=accountant,
        concurrency=RunConcurrency.create(bound),
    )


def _rec(start, dur):
    return ToolCallRecord(
        run_id="r1", name="read_file", started_at=start, finished_at=start + dur / 1000.0,
        duration_ms=dur,
    )


# ---------------------------------------------------------------------------
# the ratio itself
# ---------------------------------------------------------------------------


def test_serial_calls_score_about_one():
    # Three 100ms calls, one after another: busy 300ms over a 300ms span.
    recs = [_rec(0.0, 100.0), _rec(0.1, 100.0), _rec(0.2, 100.0)]
    assert 0.99 <= overlap_ratio(recs) <= 1.01


def test_parallel_calls_score_above_one():
    # Four 100ms calls that all ran together: busy 400ms over a ~100ms span.
    recs = [_rec(0.0, 100.0) for _ in range(4)]
    assert overlap_ratio(recs) > 3.5


def test_empty_and_degenerate_groups_score_zero():
    assert overlap_ratio([]) == 0.0
    assert overlap_ratio([_rec(0.0, 0.0)]) == 0.0


def test_cache_hit_ratio_clamps_and_rounds():
    assert cache_hit_ratio(500, 1000) == 0.5
    assert cache_hit_ratio(0, 1000) == 0.0
    assert cache_hit_ratio(2000, 1000) == 1.0
    assert cache_hit_ratio(10, 0) == 0.0


def test_estimate_tokens_is_proportional():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 240) == 100


# ---------------------------------------------------------------------------
# a real read batch measured through the ledger
# ---------------------------------------------------------------------------


def _slow_reads():
    async def slow_read(ctx, delay=0.06, i=0):
        await asyncio.sleep(delay)
        return {"ok": True, "i": i}

    TOOL_META["slow_read"] = ToolMeta("slow_read", is_read_only=True, concurrency="read_only")
    return {"slow_read": slow_read}


def test_read_batch_overlap_ratio_exceeds_one(tmp_path):
    """The Increment 0b acceptance signal: a read batch measures > 1 overlap."""
    fns = _slow_reads()
    acc = CallAccountant()
    ctx = _ctx(tmp_path, acc)
    reqs = [{"name": "slow_read", "args": {"delay": 0.06, "i": i}} for i in range(4)]
    out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns))

    group = out.concurrency_group
    assert group.startswith("read:"), "batch must tag its records with a read group"
    recs = acc.tool_calls("r1")
    assert len(recs) == 4
    assert {r.concurrency_group for r in recs} == {group}
    ratio = acc.overlap_ratio("r1", group)
    assert ratio > 1.0, f"read batch did not overlap (ratio={ratio})"
    # The batch's own measurement agrees with the ledger's.
    assert out.max_observed_concurrency > 1
    assert out.bound >= out.max_observed_concurrency


def test_overlap_ratio_is_per_group_and_whole_run(tmp_path):
    """Mutations are excluded from the read group, so the ratio stays honest."""

    async def slow_write(ctx, delay=0.05, i=0):
        await asyncio.sleep(delay)
        return {"ok": True, "i": i}

    async def slow_read(ctx, delay=0.05, i=0):
        await asyncio.sleep(delay)
        return {"ok": True, "i": i}

    TOOL_META["slow_read"] = ToolMeta("slow_read", is_read_only=True, concurrency="read_only")
    TOOL_META["slow_write"] = ToolMeta("slow_write", is_destructive=True, concurrency="state_mutating")
    try:
        acc = CallAccountant()
        ctx = _ctx(tmp_path, acc)
        reqs = [{"name": "slow_read", "args": {"delay": 0.05, "i": i}} for i in range(3)]
        reqs += [{"name": "slow_write", "args": {"delay": 0.05, "i": i}} for i in range(2)]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions={"slow_read": slow_read, "slow_write": slow_write}))

        groups = acc.tool_groups("r1")
        assert groups[""], "mutations must land in the ungrouped (serial) bucket"
        assert len(groups[""]) == 2
        assert len(groups[out.concurrency_group]) == 3
        assert acc.overlap_ratio("r1", "") < 1.01, "serial mutations must not look parallel"
        assert acc.overlap_ratio("r1", out.concurrency_group) > 1.0
        assert acc.overlap_ratio("r1") > 0.0
        assert acc.lifecycle("r1")["tool_calls"] == 5
    finally:
        TOOL_META.pop("slow_read", None)
        TOOL_META.pop("slow_write", None)


def test_tool_records_carry_payload_weight(tmp_path):
    """Tool telemetry sizes the payload, which is what the context budget needs."""
    fns = _slow_reads()
    acc = CallAccountant()
    ctx = _ctx(tmp_path, acc)
    asyncio.run(run_tool_batch(
        ctx, [{"name": "slow_read", "args": {"delay": 0.001, "i": 0}}], functions=fns
    ))
    rec = acc.tool_calls("r1")[0]
    assert rec.result_chars > 0
    assert rec.est_result_tokens == estimate_tokens('{"ok": true, "i": 0}') or rec.est_result_tokens > 0
    assert rec.is_error is False
    assert rec.duration_ms >= 0.0


def test_error_results_are_flagged(tmp_path):
    async def bad(ctx):
        return {"ok": False, "error": "no such file"}

    TOOL_META["bad"] = ToolMeta("bad", is_read_only=True, concurrency="read_only")
    try:
        acc = CallAccountant()
        ctx = _ctx(tmp_path, acc)
        asyncio.run(run_tool_batch(ctx, [{"name": "bad", "args": {}}], functions={"bad": bad}))
        assert acc.tool_calls("r1")[0].is_error is True
    finally:
        TOOL_META.pop("bad", None)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


class _StubDB:
    def __init__(self):
        self.tools: list[dict] = []
        self.llm: list[dict] = []

    async def add_tool_calls(self, rows):
        self.tools.extend(rows)

    async def add_llm_calls(self, rows):
        self.llm.extend(rows)


def test_flush_carries_concurrency_group_to_storage(tmp_path):
    fns = _slow_reads()
    acc = CallAccountant()
    ctx = _ctx(tmp_path, acc)
    out = asyncio.run(run_tool_batch(
        ctx, [{"name": "slow_read", "args": {"delay": 0.005, "i": i}} for i in range(2)],
        functions=fns,
    ))
    acc.record_llm_call(LlmCallRecord(run_id="r1", session_id="s1", duration_ms=12.0,
                                      input_tokens=100, cached_input_tokens=40))
    db = _StubDB()
    assert acc.usage_totals("r1")["cache_hit_ratio"] == 0.4
    asyncio.run(acc.flush(db, "r1"))
    assert len(db.tools) == 2
    assert {r["concurrency_group"] for r in db.tools} == {out.concurrency_group}
    assert db.tools[0]["result_chars"] > 0
    assert acc.tool_calls("r1") == [], "flush must detach the buffer"
