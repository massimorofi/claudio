"""Issue #58 Increment 1 -- bounded parallel execution & deterministic mutations.

ADK already runs a turn's function calls concurrently; this pins that Claudio's
wrapper makes that safe: independent read-only calls overlap (wall time well
under their sequential sum) under a bounded semaphore, state-mutating calls
serialize (no overlap), and the ToolContext ends in the same state regardless
of the order concurrent calls complete in.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue58_concurrency.py -q
"""
from __future__ import annotations

import asyncio
import time

from claudio_core.context import RunConcurrency, ToolContext
from claudio_core.settings import AppConfig
from claudio_core.tools import registry as reg
from claudio_core.tools.registry import TOOL_META, TOOL_FUNCTIONS, ToolMeta

from claudio_backend.engine.adk_engine import make_adk_tool


def _ctx(tmp_path, concurrency):
    return ToolContext(
        config=AppConfig(),
        session_id="s1",
        workspace=tmp_path,
        approval_mode="off",
        db=None,
        run_id="r1",
        concurrency=concurrency,
    )


def _register_slow_tools():
    """Inject two artificial tools (one read-only, one mutating) that sleep."""

    async def slow_read(ctx, path="x", delay=0.2):
        await asyncio.sleep(delay)
        return {"ok": True, "content": "data"}

    async def slow_write(ctx, path="x", delay=0.2):
        await asyncio.sleep(delay)
        ctx.deliverable_written = True
        return {"ok": True, "written": path}

    TOOL_FUNCTIONS["slow_read"] = slow_read
    TOOL_FUNCTIONS["slow_write"] = slow_write
    TOOL_META["slow_read"] = ToolMeta("slow_read", is_read_only=True, concurrency="read_only")
    TOOL_META["slow_write"] = ToolMeta("slow_write", is_destructive=True, concurrency="state_mutating")
    return ("slow_read", "slow_write")


def _unregister(names):
    for n in names:
        TOOL_FUNCTIONS.pop(n, None)
        TOOL_META.pop(n, None)


def test_read_only_calls_overlap(tmp_path):
    names = _register_slow_tools()
    try:
        conc = RunConcurrency.create(max_read_concurrency=4)
        ctx = _ctx(tmp_path, conc)
        tool = make_adk_tool("slow_read", ctx, require_confirmation=False)

        async def go():
            start = time.monotonic()
            await asyncio.gather(tool.func(delay=0.2), tool.func(delay=0.2))
            return time.monotonic() - start

        elapsed = asyncio.run(go())
        # Two 0.2s reads concurrently ~= 0.2s, not 0.4s.
        assert elapsed < 0.35, f"reads did not overlap: {elapsed:.3f}s"
    finally:
        _unregister(names)


def test_state_mutating_calls_serialize(tmp_path):
    names = _register_slow_tools()
    try:
        conc = RunConcurrency.create(max_read_concurrency=4)
        ctx = _ctx(tmp_path, conc)
        tool = make_adk_tool("slow_write", ctx, require_confirmation=False)

        async def go():
            start = time.monotonic()
            await asyncio.gather(tool.func(delay=0.15), tool.func(delay=0.15))
            return time.monotonic() - start

        elapsed = asyncio.run(go())
        # Two 0.15s mutations serialized ~= 0.3s, not 0.15s.
        assert elapsed >= 0.28, f"mutations did not serialize: {elapsed:.3f}s"
    finally:
        _unregister(names)


def test_read_semaphore_bounds_concurrency(tmp_path):
    names = _register_slow_tools()
    try:
        conc = RunConcurrency.create(max_read_concurrency=2)
        ctx = _ctx(tmp_path, conc)
        tool = make_adk_tool("slow_read", ctx, require_confirmation=False)
        peak = {"n": 0, "cur": 0}
        orig = TOOL_FUNCTIONS["slow_read"]

        async def counting(ctx, path="x", delay=0.1):
            peak["cur"] += 1
            peak["n"] = max(peak["n"], peak["cur"])
            await asyncio.sleep(delay)
            peak["cur"] -= 1
            return {"ok": True}

        TOOL_FUNCTIONS["slow_read"] = counting
        tool = make_adk_tool("slow_read", ctx, require_confirmation=False)
        try:
            async def go():
                await asyncio.gather(*[tool.func(delay=0.1) for _ in range(6)])
            asyncio.run(go())
        finally:
            TOOL_FUNCTIONS["slow_read"] = orig
        # Semaphore capped at 2 -> never more than 2 in flight.
        assert peak["n"] <= 2
    finally:
        _unregister(names)


def test_ctx_final_state_deterministic(tmp_path):
    """Concurrent mutations produce the same final ctx regardless of order."""
    names = _register_slow_tools()
    try:
        conc = RunConcurrency.create(max_read_concurrency=4)
        ctx = _ctx(tmp_path, conc)
        tool = make_adk_tool("slow_write", ctx, require_confirmation=False)

        async def go():
            await asyncio.gather(*[tool.func(delay=0.05) for _ in range(5)])

        asyncio.run(go())
        # deliverable_written is a bool set by each; final state is deterministic.
        assert ctx.deliverable_written is True
    finally:
        _unregister(names)


def test_tool_meta_concurrency_classes():
    assert TOOL_META["read_file"].concurrency == "read_only"
    assert TOOL_META["write_file"].concurrency == "state_mutating"
    assert TOOL_META["edit_file"].concurrency == "state_mutating"
    assert TOOL_META["rewind_files"].concurrency == "exclusive"
    assert TOOL_META["run_command"].concurrency == "state_mutating"


def test_run_concurrency_create_bounds():
    conc = RunConcurrency.create(3)
    assert conc.read_semaphore._value == 3


# ---------------------------------------------------------------------------
# Increment 1 (issue #58): the batch scheduler, the reducer, cancellation
# ---------------------------------------------------------------------------

from claudio_core.tool_batch import (  # noqa: E402
    HARD_MAX_READ_CONCURRENCY,
    NonParallelToolError,
    apply_tool_context,
    classify_tool,
    is_parallel_safe,
    reduce_tool_context,
    resolve_read_bound,
    run_parallel_reads_only,
    run_tool_batch,
)

#: Tools that must never be scheduled concurrently.
MUTATING_TOOLS = (
    "write_file",
    "edit_file",
    "run_command",
    "rewind_files",
    "todo_write",
    "create_scheduled_task",
    "delegate_task",
)


def _register_instrumented_reads():
    """Inject read/mutate tools whose concurrency and order are observable.

    ``slow_read`` sleeps for ``delay`` and reports back which request it served,
    so a test can prove results come back in request order even when the sleeps
    make them finish in a different order.
    """
    live = {"read_cur": 0, "read_peak": 0, "write_cur": 0, "write_peak": 0}

    async def slow_read(ctx, delay=0.05, i=-1):
        live["read_cur"] += 1
        live["read_peak"] = max(live["read_peak"], live["read_cur"])
        try:
            await asyncio.sleep(delay)
        finally:
            live["read_cur"] -= 1
        return {"ok": True, "i": i}

    async def slow_write(ctx, delay=0.05, i=-1):
        live["write_cur"] += 1
        live["write_peak"] = max(live["write_peak"], live["write_cur"])
        try:
            await asyncio.sleep(delay)
        finally:
            live["write_cur"] -= 1
        ctx.turns_since_todo += 1
        return {"ok": True, "i": i}

    fns = {"slow_read": slow_read, "slow_write": slow_write}
    TOOL_META["slow_read"] = ToolMeta("slow_read", is_read_only=True, concurrency="read_only")
    TOOL_META["slow_write"] = ToolMeta("slow_write", is_destructive=True, concurrency="state_mutating")
    return fns, live


def _drop_instrumented():
    TOOL_META.pop("slow_read", None)
    TOOL_META.pop("slow_write", None)


def test_four_reads_run_in_parallel_under_the_bound(tmp_path):
    """Max observed concurrency is > 1 (real overlap) and never above the bound."""
    fns, live = _register_instrumented_reads()
    try:
        conc = RunConcurrency.create(max_read_concurrency=4)
        ctx = _ctx(tmp_path, conc)
        reqs = [{"name": "slow_read", "args": {"delay": 0.06, "i": i}} for i in range(4)]

        async def go():
            start = time.monotonic()
            out = await run_tool_batch(ctx, reqs, functions=fns)
            return out, time.monotonic() - start

        out, elapsed = asyncio.run(go())
        assert out.max_observed_concurrency > 1, "4-read batch did not overlap at all"
        assert out.max_observed_concurrency <= 4, "read bound exceeded"
        assert live["read_peak"] == out.max_observed_concurrency
        # 4 x 0.06s in parallel ~= 0.06s; sequential would be >= 0.24s.
        assert elapsed < 0.20, f"batch was not parallel: {elapsed:.3f}s"
        assert out.parallel_count == 4 and out.sequential_count == 0
    finally:
        _drop_instrumented()


def test_bound_is_hard_even_with_more_reads_than_slots(tmp_path):
    fns, live = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=2))
        reqs = [{"name": "slow_read", "args": {"delay": 0.05, "i": i}} for i in range(6)]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns))
        assert out.max_observed_concurrency <= 2, "semaphore bound exceeded"
        assert live["read_peak"] <= 2
        assert len(out.results) == 6
    finally:
        _drop_instrumented()


def test_read_bound_is_configurable_and_clamped():
    assert resolve_read_bound(None, 6) == 6
    # Never more than the hard ceiling, never less than one.
    assert resolve_read_bound(None, 99) == HARD_MAX_READ_CONCURRENCY
    assert resolve_read_bound(None, 0) == 1
    ctx = _ctx(__import__("pathlib").Path("."), None)
    assert resolve_read_bound(ctx) == ctx.config.agent.max_read_concurrency


def test_results_come_back_in_original_request_order(tmp_path):
    """Completion order is the reverse of request order; results are not."""
    fns, _ = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
        # First request is the slowest, so it finishes last.
        delays = [0.20, 0.15, 0.05, 0.01]
        reqs = [{"name": "slow_read", "args": {"delay": d, "i": i}} for i, d in enumerate(delays)]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns))

        assert [r.index for r in out.results] == [0, 1, 2, 3]
        assert [r.payload["i"] for r in out.results] == [0, 1, 2, 3]
    finally:
        _drop_instrumented()


def test_reducer_output_is_byte_identical_across_two_runs(tmp_path):
    """Same inputs -> byte-identical canonical context, whatever the order."""
    fns, _ = _register_instrumented_reads()
    try:
        reqs = [{"name": "slow_read", "args": {"delay": d, "i": i}}
                for i, d in enumerate([0.02, 0.10, 0.01, 0.06])]

        async def once():
            ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
            out = await run_tool_batch(ctx, reqs, functions=fns)
            return apply_tool_context(ctx, out.results, turn_id="turn-1")

        a, b = asyncio.run(once()), asyncio.run(once())
        assert a.text == b.text, "reducer output drifted between runs"
        assert a.digest == b.digest
        # The ctx carries the same canonical block both times.
        assert a.text.startswith("{") and '"entries":[' in a.text
    finally:
        _drop_instrumented()


def test_reducer_is_independent_of_completion_order(tmp_path):
    """Feeding the same results in a different order yields the same bytes."""
    fns, _ = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
        reqs = [{"name": "slow_read", "args": {"delay": d, "i": i}}
                for i, d in enumerate([0.05, 0.01, 0.09, 0.02])]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns))
        forward = reduce_tool_context(out.results, turn_id="t")
        backward = reduce_tool_context(list(reversed(out.results)), turn_id="t")
        shuffled = reduce_tool_context(list(reversed(out.results[1:])) + out.results[:1], turn_id="t")
        assert forward.text == backward.text == shuffled.text
        assert [e["index"] for e in forward.entries] == sorted(e["index"] for e in forward.entries)
    finally:
        _drop_instrumented()


def test_reducer_dedupes_and_size_caps_deterministically(tmp_path):
    fns, _ = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
        reqs = [
            {"name": "slow_read", "args": {"delay": 0.01, "i": 0}},
            {"name": "slow_read", "args": {"delay": 0.01, "i": 0}},  # duplicate of index 0
            {"name": "slow_read", "args": {"delay": 0.01, "i": 2}},
        ]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns))
        full = reduce_tool_context(out.results, turn_id="t")
        assert full.payload["counts"]["deduped"] == 1
        assert full.payload["deduped_indices"] == [1]
        assert [e["index"] for e in full.entries] == [0, 2]

        capped = reduce_tool_context(out.results, turn_id="t", max_chars=1)
        assert capped.payload["counts"]["dropped"] == len(full.entries)
        assert capped.dropped == capped.payload["dropped_indices"]
        # Deterministic even under the cap.
        assert capped.text == reduce_tool_context(list(reversed(out.results)), turn_id="t", max_chars=1).text
    finally:
        _drop_instrumented()


def test_cancellation_aborts_inflight_reads_and_keeps_completed(tmp_path):
    """Cancel mid-batch: in-flight reads abort, finished ones survive, reducer stable."""
    fns, live = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
        reqs = [{"name": "slow_read", "args": {"delay": d, "i": i}}
                for i, d in enumerate([0.01, 0.60, 0.02, 0.60])]
        cancel = asyncio.Event()

        async def go():
            task = asyncio.ensure_future(
                run_tool_batch(ctx, reqs, functions=fns, cancel_event=cancel)
            )
            await asyncio.sleep(0.12)
            cancel.set()
            started = time.monotonic()
            out = await task
            # Aborted, not awaited-to-completion: the slow reads must not run out.
            assert time.monotonic() - started < 0.4, "cancel did not abort in-flight reads"
            return out

        out = asyncio.run(go())
        assert out.cancelled is True
        statuses = [r.status for r in out.results]
        assert [r.index for r in out.results] == [0, 1, 2, 3]
        assert statuses[0] == "ok" and statuses[2] == "ok", statuses
        assert statuses[1] == "cancelled" and statuses[3] == "cancelled", statuses
        assert live["read_peak"] <= 4

        reduced = apply_tool_context(ctx, out.results, turn_id="t")
        assert reduced.payload["cancelled"] is True
        assert reduced.payload["counts"]["cancelled"] == 2
        assert reduced.payload["counts"]["ok"] == 2
        # Cancelled entries carry no payload but are still present, in order.
        by_index = {e["index"]: e for e in reduced.entries}
        assert by_index[1]["payload"] == "" and by_index[1]["status"] == "cancelled"
        # And the fold is still byte-identical when replayed from the same results.
        assert reduced.text == reduce_tool_context(list(reversed(out.results)), turn_id="t").text
        assert ctx.tool_context_digest == reduced.digest
    finally:
        _drop_instrumented()


def test_cancellation_prevents_unstarted_reads_from_running(tmp_path):
    fns, live = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=1))
        cancel = asyncio.Event()
        cancel.set()  # already cancelled before the batch starts
        reqs = [{"name": "slow_read", "args": {"delay": 0.01, "i": i}} for i in range(3)]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns, cancel_event=cancel))
        assert [r.status for r in out.results] == ["skipped", "skipped", "skipped"]
        assert live["read_peak"] == 0, "cancelled batch still executed reads"
    finally:
        _drop_instrumented()


def test_mutating_tools_are_excluded_from_the_parallel_path(tmp_path):
    """The classifier is the only door: no mutating tool can be scheduled parallel."""
    ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
    for name in MUTATING_TOOLS:
        assert not is_parallel_safe(ctx, name), f"{name} must not be parallel-safe"
        assert classify_tool(ctx, name) != "read_only", f"{name} misclassified"
    for name in ("read_file", "list_dir", "batch_read", "search_with_context", "locate_symbol"):
        assert is_parallel_safe(ctx, name), f"{name} should be parallel-safe"

    # Forcing a mutating tool onto the read-only entry point is an error, not a
    # silent downgrade.
    async def go():
        for name in MUTATING_TOOLS:
            try:
                await run_parallel_reads_only(ctx, [{"name": name, "args": {}}])
            except NonParallelToolError:
                continue
            raise AssertionError(f"{name} was allowed on the parallel path")

    asyncio.run(go())


def test_mutations_in_a_mixed_batch_never_overlap(tmp_path):
    """Reads fan out; the writes interleaved with them stay one-at-a-time."""
    fns, live = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
        reqs = [
            {"name": "slow_read", "args": {"delay": 0.03, "i": 0}},
            {"name": "slow_read", "args": {"delay": 0.03, "i": 1}},
            {"name": "slow_write", "args": {"delay": 0.03, "i": 2}},
            {"name": "slow_read", "args": {"delay": 0.03, "i": 3}},
            {"name": "slow_write", "args": {"delay": 0.03, "i": 4}},
        ]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns))
        assert live["write_peak"] == 1, "mutations overlapped"
        assert live["read_peak"] > 1, "reads failed to overlap"
        assert out.max_observed_concurrency <= 4
        assert out.sequential_count == 2 and out.parallel_count == 3
        assert [r.index for r in out.results] == [0, 1, 2, 3, 4]
        assert [r.parallel for r in out.results] == [True, True, False, True, False]
        # A read that follows a write waits for it: the write is applied first.
        assert ctx.turns_since_todo == 2
    finally:
        _drop_instrumented()


def test_read_only_registry_entries_are_actually_side_effect_free():
    """The parallel allow-list is derived from read-only, non-destructive tools."""
    for name, meta in TOOL_META.items():
        if meta.concurrency == "read_only":
            assert meta.is_read_only and not meta.is_destructive, f"{name} mislabeled"
        else:
            assert not meta.is_read_only or meta.concurrency != "read_only"


def test_approval_gated_reads_stay_sequential(tmp_path):
    """Under ``always`` every call needs approval, so nothing runs in parallel."""
    fns, live = _register_instrumented_reads()
    try:
        ctx = _ctx(tmp_path, RunConcurrency.create(max_read_concurrency=4))
        ctx.approval_mode = "always"
        assert classify_tool(ctx, "slow_read") == "approval_gated"
        reqs = [{"name": "slow_read", "args": {"delay": 0.02, "i": i}} for i in range(3)]
        out = asyncio.run(run_tool_batch(ctx, reqs, functions=fns))
        assert live["read_peak"] <= 1, "approval-gated calls ran concurrently"
        assert out.parallel_count == 0 and out.sequential_count == 3
        assert [r.index for r in out.results] == [0, 1, 2]
    finally:
        _drop_instrumented()
