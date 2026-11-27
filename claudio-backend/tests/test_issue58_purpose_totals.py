"""Issue #58 Increment 0a(c) -- run totals are provably complete per purpose.

The claim under test is narrow and hard: for any run, breaking the ledger down
by ``purpose`` and summing the rows must reproduce ``usage_totals`` exactly --
calls, errors and every token field. If it does not, some call is being recorded
somewhere the breakdown cannot see, i.e. an optimization could "save" tokens by
moving work into retries, compaction or background calls instead of doing less
work.

Covers:

* ``CallAccountant.usage_totals_by_purpose`` on a mixed ledger (primary ok,
  primary error, a retry at ``attempt=2``, router, tier-2 compaction, learning,
  delegate, a cancelled call);
* ``CallAccountant.reconcile`` returning all-zero deltas for that ledger, and
  for a run with no calls at all (zero rows, no crash);
* the flush path: a second ``flush()`` writes zero duplicate rows, so the
  persisted ledger cannot double-count;
* the seams that surface it -- ``RunManager._fold_accounting`` (run report) and
  ``claudio_backend.bench`` (benchmark trials).

Run:
    cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \\
        python3 -m pytest tests/test_issue58_purpose_totals.py \\
        -p no:cacheprovider -o asyncio_mode=strict
"""
from __future__ import annotations

import pytest

from claudio_core.accounting import (
    PURPOSE_TOTAL_FIELDS,
    CallAccountant,
    LlmCallRecord,
    ToolCallRecord,
    purpose_rows,
    sum_purpose_rows,
)
from claudio_core.bench import summarize

from claudio_backend.bench import (
    merge_breakdowns,
    purpose_breakdown,
    render_purpose_markdown,
    trial_from_run,
)
from claudio_backend.runs.manager import RunManager, RunRecord


# ---------------------------------------------------------------------------
# stdlib fake db -- duck-typed for exactly what CallAccountant.flush() calls
# ---------------------------------------------------------------------------


class FakeDB:
    """Minimal stand-in for ``claudio_core.db.Database`` (ledger writes only).

    Records every batch handed to ``add_llm_calls`` / ``add_tool_calls`` so a
    test can assert on the *total* number of persisted rows across several
    flushes -- the only way to catch double counting at the persistence seam.
    """

    def __init__(self) -> None:
        self.llm_rows: list[dict] = []
        self.tool_rows: list[dict] = []
        self.llm_calls = 0
        self.tool_calls = 0

    async def add_llm_calls(self, rows: list[dict]) -> int:
        self.llm_calls += 1
        self.llm_rows.extend(rows)
        return len(rows)

    async def add_tool_calls(self, rows: list[dict]) -> int:
        self.tool_calls += 1
        self.tool_rows.extend(rows)
        return len(rows)


def _mixed_ledger(run_id: str = "run-1") -> CallAccountant:
    """One ledger covering every purpose shape a real run produces.

    Deliberately mixes outcomes (ok / error / cancelled), a retry at
    ``attempt=2``, background purposes (router, compaction, learning,
    delegate) and one *unmeasured* call, because those are exactly the calls an
    incomplete ledger tends to lose.
    """
    acc = CallAccountant()
    # primary, clean success, cache hit
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="primary", model_role="primary",
            input_tokens=1000, cached_input_tokens=600, output_tokens=120,
            reasoning_tokens=30, cost_usd=0.0040,
        )
    )
    # primary, failed attempt (still a real call with real spend)
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="primary", model_role="primary",
            outcome="error", error_code="rate_limited", attempt=1,
            input_tokens=200, cached_input_tokens=0, output_tokens=0,
            cost_usd=0.0002,
        )
    )
    # the retry of that same logical call: attempt=2
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="primary", model_role="primary",
            attempt=2, input_tokens=1000, cached_input_tokens=900,
            output_tokens=60, reasoning_tokens=10, cost_usd=0.0025,
        )
    )
    # router (fast profile classifier)
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="router", model_role="fast",
            input_tokens=80, cached_input_tokens=0, output_tokens=2,
            cost_usd=0.0001,
        )
    )
    # tier-2 compaction (LLM summary of the transcript)
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="compact", model_role="fast",
            input_tokens=4000, cached_input_tokens=1000, output_tokens=300,
            cost_usd=0.0060,
        )
    )
    # background learning capture
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="learning", model_role="fast",
            input_tokens=500, cached_input_tokens=0, output_tokens=40,
            cost_usd=0.0008,
        )
    )
    # delegated worker call
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="delegate", model_role="worker",
            input_tokens=300, cached_input_tokens=100, output_tokens=25,
            cost_usd=0.0005,
        )
    )
    # cancelled mid-flight: counts as a call, carries no measured usage
    acc.record_llm_call(
        LlmCallRecord(
            run_id=run_id, purpose="primary", outcome="cancelled",
            measured=False, input_tokens=0, output_tokens=0, cost_usd=0.0,
        )
    )
    acc.bump_lifecycle(run_id, "tool_calls", 3)
    acc.bump_lifecycle(run_id, "tier2_compactions", 1)
    return acc


# ---------------------------------------------------------------------------
# the breakdown itself
# ---------------------------------------------------------------------------

# Expected per-purpose figures for _mixed_ledger(), written out by hand so the
# test fails if the implementation quietly re-buckets a call.
EXPECTED = {
    "primary": {
        "calls": 4, "errors": 1, "cancelled": 1,
        "input_tokens": 2200, "output_tokens": 180,
        "cached_input_tokens": 1500, "reasoning_tokens": 40,
        "cost_usd": 0.0067,
    },
    "compact": {
        "calls": 1, "errors": 0, "cancelled": 0,
        "input_tokens": 4000, "output_tokens": 300,
        "cached_input_tokens": 1000, "reasoning_tokens": 0,
        "cost_usd": 0.0060,
    },
    "router": {
        "calls": 1, "errors": 0, "cancelled": 0,
        "input_tokens": 80, "output_tokens": 2,
        "cached_input_tokens": 0, "reasoning_tokens": 0,
        "cost_usd": 0.0001,
    },
    "learning": {
        "calls": 1, "errors": 0, "cancelled": 0,
        "input_tokens": 500, "output_tokens": 40,
        "cached_input_tokens": 0, "reasoning_tokens": 0,
        "cost_usd": 0.0008,
    },
    "delegate": {
        "calls": 1, "errors": 0, "cancelled": 0,
        "input_tokens": 300, "output_tokens": 25,
        "cached_input_tokens": 100, "reasoning_tokens": 0,
        "cost_usd": 0.0005,
    },
}


def test_by_purpose_rows_are_correct_per_purpose():
    acc = _mixed_ledger()
    by = acc.usage_totals_by_purpose("run-1")

    assert set(by) == set(EXPECTED)
    for purpose, want in EXPECTED.items():
        row = by[purpose]
        assert row["purpose"] == purpose, "every row is purpose-tagged"
        for field, value in want.items():
            assert row[field] == pytest.approx(value), f"{purpose}.{field}"
        # one row per purpose present -- nothing pre-seeded, nothing merged
        assert set(row) == {
            "purpose", *PURPOSE_TOTAL_FIELDS, "cache_hit_ratio"
        }


def test_by_purpose_rows_sorted_by_calls_desc():
    rows = purpose_rows(_mixed_ledger().usage_totals_by_purpose("run-1"))
    calls = [r["calls"] for r in rows]
    assert calls == sorted(calls, reverse=True)
    assert rows[0]["purpose"] == "primary" and rows[0]["calls"] == 4
    # ties are broken by purpose name, so the order is deterministic
    assert [r["purpose"] for r in rows[1:]] == [
        "compact", "delegate", "learning", "router"
    ]


def test_by_purpose_sums_equal_run_totals():
    acc = _mixed_ledger()
    totals = acc.usage_totals("run-1")
    rows = purpose_rows(acc.usage_totals_by_purpose("run-1"))

    assert sum(r["calls"] for r in rows) == totals["calls"] == 8
    assert sum(r["errors"] for r in rows) == totals["errors"] == 1
    for field in ("input_tokens", "output_tokens", "cached_input_tokens",
                  "reasoning_tokens"):
        assert sum(r[field] for r in rows) == totals[field], field
    assert sum(r["cost_usd"] for r in rows) == pytest.approx(totals["cost_usd"])


def test_only_measured_calls_contribute_tokens():
    """The cancelled (unmeasured) call counts as a call but adds no tokens."""
    acc = _mixed_ledger()
    row = acc.usage_totals_by_purpose("run-1")["primary"]
    assert row["calls"] == 4
    assert row["cancelled"] == 1
    # 1000 + 200 + 1000 measured input tokens; the cancelled call adds none
    assert row["input_tokens"] == 2200


def test_per_purpose_cache_hit_ratio_matches_run_totals():
    acc = _mixed_ledger()
    totals = acc.usage_totals("run-1")
    by = acc.usage_totals_by_purpose("run-1")

    cached = sum(r["cached_input_tokens"] for r in by.values())
    tin = sum(r["input_tokens"] for r in by.values())
    assert cached == totals["cached_input_tokens"]
    assert tin == totals["input_tokens"]
    assert totals["cache_hit_ratio"] == pytest.approx(round(cached / tin, 4))
    # each row carries its own ratio, computed the same way
    assert by["primary"]["cache_hit_ratio"] == pytest.approx(
        round(1500 / 2200, 4)
    )
    assert by["router"]["cache_hit_ratio"] == 0.0


def test_reconcile_is_exactly_zero_on_a_mixed_ledger():
    acc = _mixed_ledger()
    delta = acc.reconcile("run-1")

    assert set(delta) == {*PURPOSE_TOTAL_FIELDS, "ok"}
    for field in PURPOSE_TOTAL_FIELDS:
        assert delta[field] == 0, f"{field} leaked between breakdown and totals"
    assert delta["ok"] is True


def test_call_with_no_purpose_lands_in_other_and_still_balances():
    """Nothing may fall through the buckets: an unlabelled call is "other".

    Without that fallback an unlabelled call would count in ``usage_totals``
    but appear in no row, which is precisely the leak ``reconcile`` exists to
    catch -- so it gets its own test.
    """
    acc = _mixed_ledger()
    acc.record_llm_call(
        LlmCallRecord(run_id="run-1", purpose="", input_tokens=7,
                      output_tokens=1, cost_usd=0.00001)
    )
    by = acc.usage_totals_by_purpose("run-1")
    assert "other" in by
    assert by["other"]["calls"] == 1
    assert by["other"]["input_tokens"] == 7
    assert acc.usage_totals("run-1")["calls"] == 9
    delta = acc.reconcile("run-1")
    assert delta["ok"] is True
    assert delta["calls"] == 0 and delta["input_tokens"] == 0


# ---------------------------------------------------------------------------
# persistence: no double counting across flushes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_writes_each_row_once_and_second_flush_writes_none():
    acc = _mixed_ledger()
    rows_before = purpose_rows(acc.usage_totals_by_purpose("run-1"))
    db = FakeDB()

    n1 = await acc.flush(db, "run-1")
    assert n1 == 8
    assert db.llm_calls == 1
    assert len(db.llm_rows) == 8

    # A second flush (the manager flushes again after post-run capture) must
    # write nothing: the buffer was detached by the first successful write.
    n2 = await acc.flush(db, "run-1")
    assert n2 == 0
    assert db.llm_calls == 1, "add_llm_calls must not be called again"
    assert len(db.llm_rows) == 8, "no duplicate rows"

    await acc.flush(db, "run-1")
    assert len(db.llm_rows) == 8


@pytest.mark.asyncio
async def test_flushed_rows_group_by_purpose_to_the_same_totals():
    """The rows on disk, regrouped, equal the breakdown computed pre-flush."""
    acc = _mixed_ledger()
    acc.record_tool_call(ToolCallRecord(run_id="run-1", name="read_file"))
    acc.record_tool_call(ToolCallRecord(run_id="run-1", name="run_command"))
    db = FakeDB()
    expected = acc.usage_totals_by_purpose("run-1")
    expected_calls = {p: r["calls"] for p, r in expected.items()}

    await acc.flush(db, "run-1")

    on_disk: dict[str, int] = {}
    for row in db.llm_rows:
        on_disk[row["purpose"] or "other"] = (
            on_disk.get(row["purpose"] or "other", 0) + 1
        )
    assert on_disk == expected_calls
    assert len(db.tool_rows) == 2

    # After the flush the buffer is detached, so the live breakdown is empty and
    # still reconciles to zero -- an empty run is not an inconsistent run.
    assert acc.usage_totals_by_purpose("run-1") == {}
    assert acc.reconcile("run-1")["ok"] is True


@pytest.mark.asyncio
async def test_flush_all_runs_keeps_per_run_breakdowns_separate():
    acc = _mixed_ledger("run-1")
    acc.record_llm_call(
        LlmCallRecord(run_id="run-2", purpose="router", input_tokens=10)
    )
    db = FakeDB()
    assert await acc.flush(db) == 9
    assert {r["run_id"] for r in db.llm_rows} == {"run-1", "run-2"}
    assert len(db.llm_rows) == 9
    await acc.flush(db)
    assert len(db.llm_rows) == 9, "a flush-all must not rewrite committed runs"
    # per-run breakdowns stay separate: run-2's router call never joins run-1
    assert acc.usage_totals_by_purpose("run-1") == {}
    assert acc.usage_totals_by_purpose("run-2") == {}


# ---------------------------------------------------------------------------
# the empty run
# ---------------------------------------------------------------------------


def test_run_with_no_calls_yields_no_rows_and_zero_reconcile():
    acc = CallAccountant()
    assert acc.usage_totals_by_purpose("nothing") == {}
    assert purpose_rows(acc.usage_totals_by_purpose("nothing")) == []
    delta = acc.reconcile("nothing")
    assert delta["ok"] is True
    for field in PURPOSE_TOTAL_FIELDS:
        assert delta[field] == 0
    assert acc.usage_totals("nothing")["calls"] == 0


@pytest.mark.asyncio
async def test_empty_run_flushes_zero_rows_without_crashing():
    acc = CallAccountant()
    db = FakeDB()
    assert await acc.flush(db, "nothing") == 0
    assert db.llm_rows == [] and db.tool_rows == []
    assert db.llm_calls == 0
    assert acc.reconcile("nothing")["ok"] is True


# ---------------------------------------------------------------------------
# the seams that surface it
# ---------------------------------------------------------------------------


def _manager_with(acc: CallAccountant) -> RunManager:
    """A RunManager carrying only the ledger (the rest of it is unused here)."""
    mgr = object.__new__(RunManager)
    mgr.accountant = acc
    mgr.metrics = None
    return mgr


def test_fold_accounting_attaches_by_purpose_and_delta():
    acc = _mixed_ledger()
    mgr = _manager_with(acc)
    record = RunRecord(run_id="run-1", session_id="s1")

    totals = mgr._fold_accounting(record)  # noqa: SLF001 - the seam under test

    assert record.model_calls == 8
    assert [r["purpose"] for r in record.by_purpose] == [
        "primary", "compact", "delegate", "learning", "router"
    ]
    assert sum(r["calls"] for r in record.by_purpose) == record.model_calls
    assert record.purpose_reconcile["ok"] is True
    # additive on the returned totals: existing keys keep their meaning
    assert totals["calls"] == 8
    assert totals["by_purpose"] == record.by_purpose
    assert totals["purpose_reconcile"] is record.purpose_reconcile


def test_fold_accounting_on_a_run_with_no_calls():
    mgr = _manager_with(CallAccountant())
    record = RunRecord(run_id="empty", session_id="s1")
    totals = mgr._fold_accounting(record)  # noqa: SLF001
    assert record.by_purpose == []
    assert record.model_calls == 0
    assert totals["purpose_reconcile"]["ok"] is True


def test_bench_trial_carries_purpose_breakdown():
    acc = _mixed_ledger()
    record = RunRecord(run_id="run-1", session_id="s1")
    trial = trial_from_run(
        task_class="edit", cold=False, record=record, accountant=acc,
        wall_seconds=2.0, time_to_first_output=0.5, verified=True,
    )
    assert trial.model_calls == 8

    breakdown = purpose_breakdown(acc, "run-1")
    assert sum(r["calls"] for r in breakdown["by_purpose"]) == trial.model_calls
    assert breakdown["reconcile"]["ok"] is True

    report = merge_breakdowns([breakdown, breakdown])
    assert report["reconcile"]["ok"] is True
    assert report["by_purpose"][0]["purpose"] == "primary"
    assert report["by_purpose"][0]["calls"] == 8  # 4 + 4 across two trials
    # summarize()'s own shape is untouched by the additive keys
    summary = summarize([trial])
    assert summary["overall"]["model_calls"]["median"] == 8.0
    assert "by_purpose" not in summary
    assert "purpose" in render_purpose_markdown(report)
