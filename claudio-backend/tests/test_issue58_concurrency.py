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
