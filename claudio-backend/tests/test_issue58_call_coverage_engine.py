"""Issue #58 Increment 0a(b)-ia -- exactly one accounting record per *engine* call.

``claudio_backend.engine.reasoning_llm._patch_max_tokens_retry`` is the engine
counterpart of ``claudio_core.llm.openai_chat_completion``: every HTTP call the
ADK engine, its worker sub-agents and the daemon agent make goes through it.
The same two defects that Increment 0a(b)-i closed on the auxiliary path were
still open here.

* **Zero records on cancellation.** ``asyncio.CancelledError`` derives from
  ``BaseException``, so the ``except Exception`` nets around
  ``chat.completions.create`` and around stream consumption never saw it and a
  cancelled run emitted *zero* records.
* **Double emission.** The max_tokens rename retry, the error branches and the
  streaming generator could each emit for one logical call. Wrapping the
  recorder in ``accounting.once_sink`` at call entry makes a second record
  structurally impossible.

``AdkEngine._record_run_cancellation`` is the engine-side backstop: a run
cancelled where no HTTP call got recorded still contributes exactly one
``cancelled`` record, and a turn that completed first is never recorded twice.

Everything runs offline: a fake openai client and a fake recorder stand in for
the provider, so no credentials and no non-loopback socket are involved.

Run:
    cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \
        python3 -m pytest tests/test_issue58_call_coverage_engine.py \
        -p no:cacheprovider -o asyncio_mode=strict
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from claudio_backend.engine.reasoning_llm import (
    ReasoningOpenAILlm,
    _patch_max_tokens_retry,
    _record_call,
)
from claudio_backend.engine.adk_engine import AdkEngine
from claudio_core.accounting import CallAccountant, once_sink, set_default_recorder

MODEL = "gpt-test"


class Recorder:
    """Fake engine recorder: keeps every payload it is handed."""

    def __init__(self, raises: bool = False) -> None:
        self.payloads: list[dict] = []
        self.raises = raises

    def __call__(self, payload: dict) -> None:
        self.payloads.append(payload)
        if self.raises:
            raise RuntimeError("recorder exploded")

    @property
    def outcomes(self) -> list[str]:
        return [p["outcome"] for p in self.payloads]


@pytest.fixture(autouse=True)
def no_default_recorder():
    """Keep the process-wide default recorder out of these tests."""
    set_default_recorder(None)
    yield
    set_default_recorder(None)


def make_client(recorder: Any, behaviour: dict):
    """Fake AsyncOpenAI whose ``chat.completions.create`` runs ``behaviour``.

    ``behaviour`` maps a call index (1-based) to either a result object or an
    exception class/instance to raise. The accounting patch is applied exactly
    as ``make_openai_client`` applies it in production.
    """
    state = {"n": 0}

    async def create(*args, **kwargs):
        state["n"] += 1
        step = behaviour.get(state["n"], behaviour.get("*"))
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step(**kwargs)
        return step

    completions = SimpleNamespace(create=create)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        _claudio_call_context={
            "recorder": recorder,
            "run_id": "run-1",
            "session_id": "sess-1",
            "purpose": "primary",
            "model_role": "primary",
            "profile_name": "default",
            "reasoning_effort": "low",
            "model": MODEL,
        },
        calls=state,
    )
    _patch_max_tokens_retry(client)
    return client


def completion(text: str = "hello"):
    """Minimal ChatCompletion stand-in carrying a full usage block."""
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=3,
            prompt_tokens_details=SimpleNamespace(cached_tokens=4),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=text, reasoning_content=None, tool_calls=None
                )
            )
        ],
    )


def bad_request(message: str) -> openai.BadRequestError:
    """A 400 built the way the OpenAI SDK builds one (no network involved)."""
    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return openai.BadRequestError(message, response=response, body=None)


MAX_TOKENS_MSG = (
    "Error: 'max_tokens' is not supported, use 'max_completion_tokens' instead."
)


async def call(client, **kwargs):
    return await client.chat.completions.create(model=MODEL, **kwargs)


# ---------------------------------------------------------------------------
# one record per logical call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ok_records_exactly_one_call():
    rec = Recorder()
    client = make_client(rec, {1: completion()})

    result = await call(client, max_tokens=32)

    assert result is not None
    assert len(rec.payloads) == 1
    payload = rec.payloads[0]
    assert payload["outcome"] == "ok"
    assert payload["error_code"] == ""
    assert payload["attempt"] == 1
    assert payload["usage"] == {
        "input_tokens": 10,
        "output_tokens": 3,
        "cached_input_tokens": 4,
        "reasoning_tokens": 1,
    }
    assert payload["purpose"] == "primary"
    assert payload["run_id"] == "run-1"
    assert payload["session_id"] == "sess-1"
    assert payload["model_role"] == "primary"
    assert payload["reasoning_effort"] == "low"
    assert payload["measured"] is True


@pytest.mark.asyncio
async def test_max_tokens_rename_records_once_with_attempt_two():
    """The rename retry is ONE logical call: 1 record, attempt=2."""
    rec = Recorder()
    client = make_client(
        rec,
        {
            1: bad_request(MAX_TOKENS_MSG),
            2: completion(),
        },
    )

    await call(client, max_tokens=32)

    assert len(rec.payloads) == 1
    assert rec.outcomes == ["ok"]
    assert rec.payloads[0]["attempt"] == 2
    assert client.calls["n"] == 2


@pytest.mark.asyncio
async def test_bad_request_after_rename_records_error_with_attempt_two():
    """Rename, then a second failure: still exactly one record, outcome=error."""
    rec = Recorder()
    client = make_client(
        rec,
        {
            1: bad_request(MAX_TOKENS_MSG),
            2: RuntimeError("gateway exploded"),
        },
    )

    with pytest.raises(RuntimeError):
        await call(client, max_tokens=32)

    assert len(rec.payloads) == 1
    assert rec.outcomes == ["error"]
    assert rec.payloads[0]["attempt"] == 2
    assert rec.payloads[0]["error_code"] == "RuntimeError"


@pytest.mark.asyncio
async def test_plain_bad_request_records_one_error():
    rec = Recorder()
    client = make_client(rec, {1: bad_request("context length exceeded")})

    with pytest.raises(openai.BadRequestError):
        await call(client, max_tokens=32)

    assert len(rec.payloads) == 1
    assert rec.outcomes == ["error"]
    assert rec.payloads[0]["attempt"] == 1
    assert rec.payloads[0]["error_code"] == "BadRequestError"


@pytest.mark.asyncio
async def test_generic_exception_records_one_error():
    rec = Recorder()
    client = make_client(rec, {1: RuntimeError("transport died")})

    with pytest.raises(RuntimeError, match="transport died"):
        await call(client, max_tokens=32)

    assert len(rec.payloads) == 1
    assert rec.outcomes == ["error"]
    assert rec.payloads[0]["error_code"] == "RuntimeError"
    assert rec.payloads[0]["usage"] is None


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------


async def _hang(**kwargs):
    """Stand-in for a request that never returns; cancellation lands here."""
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_cancellation_during_create_records_one_cancelled():
    """The bug this increment closes: a cancelled call used to record nothing."""
    rec = Recorder()
    client = make_client(rec, {"*": _hang})

    task = asyncio.create_task(call(client, max_tokens=32))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(rec.payloads) == 1
    payload = rec.payloads[0]
    assert payload["outcome"] == "cancelled"
    assert payload["error_code"] == "cancelled"
    assert payload["attempt"] == 1
    assert payload["usage"] is None
    assert payload["measured"] is False
    assert payload["run_id"] == "run-1"
    assert payload["purpose"] == "primary"


@pytest.mark.asyncio
async def test_cancellation_after_a_record_is_not_overwritten():
    """A call that already emitted cannot be re-emitted by a later cancel."""
    rec = Recorder()
    guard = once_sink(rec)
    _record_call(guard, {"purpose": "primary"}, MODEL, time.time(), 1, "ok", "", None)
    _record_call(guard, {"purpose": "primary"}, MODEL, time.time(), 1, "cancelled", "cancelled", None)

    assert len(rec.payloads) == 1
    assert rec.outcomes == ["ok"]


class FakeStream:
    """Async-iterable stream stand-in.

    Yields ``chunks`` then, when ``hang`` is set, blocks forever so the test can
    cancel the consumer mid-stream (exactly what a cancelled run does).
    """

    def __init__(self, chunks, hang: bool = False) -> None:
        self._chunks = list(chunks)
        self._hang = hang

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._hang:
            await asyncio.Event().wait()
        raise StopAsyncIteration


def text_chunk(text: str):
    return SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=text, reasoning_content=None, tool_calls=None
                ),
                finish_reason=None,
            )
        ],
    )


def usage_chunk(prompt=7, completion_n=2):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion_n,
            total_tokens=prompt + completion_n,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
        choices=[],
    )


def stream_llm(client):
    """Bound ``_generate_content_streaming`` against a fake client.

    The generator only touches ``self._openai_client``, so a shim avoids
    constructing a real ADK ``OpenAILlm`` (which would want credentials).
    """
    shim = SimpleNamespace(_openai_client=client)
    return ReasoningOpenAILlm._generate_content_streaming(
        shim, {"model": MODEL, "messages": []}
    )


@pytest.mark.asyncio
async def test_cancellation_during_streaming_records_one_cancelled():
    rec = Recorder()
    client = make_client(rec, {1: FakeStream([text_chunk("par")], hang=True)})

    async def consume():
        out = []
        async for response in stream_llm(client):
            out.append(response)
        return out

    task = asyncio.create_task(consume())
    for _ in range(6):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(rec.payloads) == 1
    payload = rec.payloads[0]
    assert payload["outcome"] == "cancelled"
    assert payload["error_code"] == "cancelled"
    assert payload["usage"] is None


@pytest.mark.asyncio
async def test_completed_stream_then_cancel_records_only_the_ok_call():
    """Completed-then-cancelled: the stream must not be recorded twice."""
    rec = Recorder()
    client = make_client(
        rec, {1: FakeStream([text_chunk("done"), usage_chunk()])}
    )

    agen = stream_llm(client)
    responses = []
    async for response in agen:
        responses.append(response)

    assert len(rec.payloads) == 1
    assert rec.outcomes == ["ok"]
    assert rec.payloads[0]["usage"]["input_tokens"] == 7

    # Cancelling after the generator finished cannot add a second record.
    await agen.aclose()
    assert len(rec.payloads) == 1


# ---------------------------------------------------------------------------
# recorder robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raising_recorder_does_not_break_a_successful_call():
    rec = Recorder(raises=True)
    client = make_client(rec, {1: completion()})

    result = await call(client, max_tokens=32)

    assert result is not None
    assert len(rec.payloads) == 1  # it fired, and the exception was swallowed


@pytest.mark.asyncio
async def test_raising_recorder_does_not_break_a_failed_call():
    rec = Recorder(raises=True)
    client = make_client(rec, {1: RuntimeError("boom")})

    with pytest.raises(RuntimeError, match="boom"):
        await call(client, max_tokens=32)

    assert len(rec.payloads) == 1


@pytest.mark.asyncio
async def test_double_fire_yields_exactly_one_stored_record():
    """The once_sink guard: two emissions for one logical call store one record."""
    rec = Recorder()
    guard = once_sink(rec)
    ctx = {"purpose": "primary", "run_id": "run-1"}

    _record_call(guard, ctx, MODEL, time.time(), 1, "ok", "", None)
    _record_call(guard, ctx, MODEL, time.time(), 2, "error", "BadRequestError", None)
    _record_call(guard, ctx, MODEL, time.time(), 2, "cancelled", "cancelled", None)

    assert len(rec.payloads) == 1
    assert rec.outcomes == ["ok"]


def test_once_sink_none_passthrough():
    assert once_sink(None) is None


# ---------------------------------------------------------------------------
# engine-side backstop (AdkEngine._record_run_cancellation)
# ---------------------------------------------------------------------------


def make_request(run_id: str = "run-9"):
    return SimpleNamespace(
        run_id=run_id,
        session_id="sess-9",
        model=SimpleNamespace(model=MODEL, reasoning_effort="medium"),
        metadata={"model_role": "primary", "profile_name": "default"},
    )


class EngineShim:
    """Just the state ``_record_run_cancellation`` touches."""

    def __init__(self, accountant):
        self.accountant = accountant
        self._cancel_guards: dict = {}

    _record_run_cancellation = AdkEngine._record_run_cancellation


def cancelled_records(accountant, run_id):
    return [r for r in accountant.llm_calls(run_id) if r.outcome == "cancelled"]


@pytest.mark.asyncio
async def test_engine_backstop_records_cancelled_when_run_has_no_records():
    accountant = CallAccountant()
    engine = EngineShim(accountant)

    engine._record_run_cancellation(make_request())

    recs = accountant.llm_calls("run-9")
    assert len(recs) == 1
    assert recs[0].outcome == "cancelled"
    assert recs[0].error_code == "cancelled"
    assert recs[0].purpose == "primary"
    assert recs[0].session_id == "sess-9"
    assert recs[0].measured is False


@pytest.mark.asyncio
async def test_engine_backstop_double_fire_records_once():
    accountant = CallAccountant()
    engine = EngineShim(accountant)
    request = make_request()

    engine._record_run_cancellation(request)
    engine._record_run_cancellation(request)
    engine._record_run_cancellation(request)

    assert len(cancelled_records(accountant, "run-9")) == 1


@pytest.mark.asyncio
async def test_engine_backstop_skips_completed_then_cancelled_turn():
    """A turn that already recorded its call is not recorded a second time."""
    accountant = CallAccountant()
    engine = EngineShim(accountant)
    request = make_request()
    sink = accountant.make_engine_recorder()
    now = time.time()
    sink(
        {
            "purpose": "primary", "model": MODEL, "run_id": "run-9",
            "session_id": "sess-9", "model_role": "primary",
            "profile_name": "default", "reasoning_effort": "medium",
            "started_at": now, "finished_at": now, "duration_ms": 12.0,
            "outcome": "ok", "error_code": "", "attempt": 1,
            "usage": {"input_tokens": 5, "output_tokens": 2,
                      "cached_input_tokens": 0, "reasoning_tokens": 0},
            "measured": True,
        }
    )

    engine._record_run_cancellation(request)

    recs = accountant.llm_calls("run-9")
    assert [r.outcome for r in recs] == ["ok"]


@pytest.mark.asyncio
async def test_engine_backstop_defers_to_client_cancelled_record():
    """reasoning_llm already recorded the in-flight call: add nothing."""
    accountant = CallAccountant()
    engine = EngineShim(accountant)
    request = make_request()
    now = time.time()
    accountant.make_engine_recorder()(
        {
            "purpose": "primary", "model": MODEL, "run_id": "run-9",
            "session_id": "sess-9", "model_role": "", "profile_name": "",
            "reasoning_effort": "", "started_at": now, "finished_at": now,
            "duration_ms": 4.0, "outcome": "cancelled",
            "error_code": "cancelled", "attempt": 1, "usage": None,
            "measured": False,
        }
    )

    engine._record_run_cancellation(request)

    assert len(cancelled_records(accountant, "run-9")) == 1


@pytest.mark.asyncio
async def test_engine_backstop_without_accountant_is_a_noop():
    engine = EngineShim(None)
    engine._record_run_cancellation(make_request())  # must not raise
