"""Issue #58 Increment 4 -- incremental streaming through the engine drive loop.

Drives ``AdkEngine._drive`` with a stub runner that emits streaming partial
deltas followed by the aggregated final event, and asserts the client receives
the text exactly once (deltas streamed, final not duplicated) and that the
non-streaming path is unchanged.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue58_streaming.py -q
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.settings import AppConfig

from claudio_backend.engine.adk_engine import AdkEngine


def _text_event(text, *, partial, author="claudio"):
    part = SimpleNamespace(
        text=text, thought=False, function_call=None, function_response=None
    )
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(
        partial=partial,
        author=author,
        content=content,
        usage_metadata=None,
        get_function_calls=lambda: [],
    )


def _request():
    return RunRequest(
        run_id="r1",
        session_id="s1",
        workspace=Path("/tmp"),
        instructions="",
        user_input="hi",
        model=ModelSpec(model="m"),
        tools=ToolPolicy(),
    )


class _FakeRunner:
    def __init__(self, events):
        self._events = events

    def run_async(self, **kwargs):
        events = self._events

        async def gen():
            for e in events:
                yield e

        return gen()


async def _collect(engine, events, streaming):
    engine.config.agent.streaming_enabled = streaming
    runner = _FakeRunner(events)
    out = []
    async for evt in engine._drive(runner, _request(), None, engine._make_context(_request())):
        out.append(evt)
    return out


def test_streaming_emits_deltas_without_duplication():
    engine = AdkEngine(AppConfig(), db=None)
    events = [
        _text_event("Hel", partial=True),
        _text_event("lo", partial=True),
        _text_event("Hello", partial=False),  # aggregated final
    ]
    out = asyncio.run(_collect(engine, events, streaming=True))
    tokens = [e["data"]["text"] for e in out if e["event"] == "token"]
    # Deltas streamed; the aggregated final is NOT re-emitted.
    assert tokens == ["Hel", "lo"]
    completed = [e for e in out if e["event"] == "run_completed"]
    assert completed and completed[0]["data"]["final_output"] == "Hello"


def test_streaming_falls_back_when_only_final_arrives():
    engine = AdkEngine(AppConfig(), db=None)
    events = [_text_event("Hello", partial=False)]
    out = asyncio.run(_collect(engine, events, streaming=True))
    tokens = [e["data"]["text"] for e in out if e["event"] == "token"]
    # No partials this turn -> the final is emitted so the client isn't starved.
    assert tokens == ["Hello"]


def test_non_streaming_unchanged():
    engine = AdkEngine(AppConfig(), db=None)
    events = [_text_event("Hello", partial=False)]
    out = asyncio.run(_collect(engine, events, streaming=False))
    tokens = [e["data"]["text"] for e in out if e["event"] == "token"]
    assert tokens == ["Hello"]
