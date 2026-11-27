"""Issue #58 Increment 0a(b)-i -- exactly one accounting record per LLM call.

``claudio_core.llm.openai_chat_completion`` is the entry point every auxiliary
model call (compaction, router, learning, fixer, the /config/llm probe) uses,
and every call through it must land in the accounting ledger **exactly once** --
including a call the caller cancels.

Two defects are pinned here:

* **Zero records on cancellation.** ``asyncio.CancelledError`` derives from
  ``BaseException``, so the old ``except Exception`` net never saw it and a
  cancelled call was recorded *zero* times.
* **Double emission.** The function has many emitting branches (success,
  transport error, HTTP >= 400, invalid JSON, missing text, unexpected shape).
  A sink wrapped in ``accounting.once_sink`` at entry makes a second record
  structurally impossible rather than relying on branches being exclusive.

Everything runs offline: ``httpx.AsyncClient`` is monkeypatched onto an
in-memory transport (``httpx.MockTransport`` / a stdlib fake), so no provider
credentials and no non-loopback socket are involved.

Run:
    cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \\
        python3 -m pytest tests/test_issue58_call_coverage.py \\
        -p no:cacheprovider -o asyncio_mode=strict
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from claudio_core.accounting import once_sink, set_default_recorder
from claudio_core.llm import LlmCallError, openai_chat_completion

BASE_URL = "http://127.0.0.1:9/v1"  # never contacted: transport is faked


class Sink:
    """Recording usage_sink: keeps every payload it is handed."""

    def __init__(self, raises: bool = False) -> None:
        self.payloads: list[dict] = []
        self.raises = raises

    def __call__(self, payload: dict) -> None:
        self.payloads.append(payload)
        if self.raises:
            raise RuntimeError("sink exploded")


def install_transport(monkeypatch, handler) -> None:
    """Force ``httpx.AsyncClient`` inside claudio_core.llm onto an in-memory transport."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def completion_body(text: str = "hello", usage: dict | None = None) -> dict:
    body = {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


async def call(sink, **kwargs) -> str:
    """One call with the boilerplate filled in."""
    kwargs.setdefault("base_url", BASE_URL)
    kwargs.setdefault("model", "gpt-test")
    kwargs.setdefault("messages", [{"role": "user", "content": "hi"}])
    kwargs.setdefault("purpose", "other")
    kwargs.setdefault("usage_sink", sink)
    return await openai_chat_completion(**kwargs)


# ---------------------------------------------------------------------------
# once_sink unit behaviour
# ---------------------------------------------------------------------------


def test_once_sink_forwards_only_the_first_payload():
    seen: list[dict] = []
    guarded = once_sink(seen.append)

    guarded({"n": 1})
    guarded({"n": 2})
    guarded({"n": 3})

    assert seen == [{"n": 1}], "once_sink must forward exactly one payload"


def test_once_sink_stays_consumed_when_the_sink_raises():
    """A raising sink is still *one* emission: the guard is set before forwarding."""
    calls: list[int] = []

    def boom(payload: dict) -> None:
        calls.append(1)
        raise RuntimeError("nope")

    guarded = once_sink(boom)
    with pytest.raises(RuntimeError):
        guarded({"a": 1})
    guarded({"a": 2})  # swallowed by llm._emit in production; here it is a no-op

    assert len(calls) == 1


def test_once_sink_passes_through_none():
    assert once_sink(None) is None


def test_once_sink_is_threadsafe_under_a_race():
    """Only one of N concurrent emissions gets through."""
    import threading

    seen: list[int] = []
    lock = threading.Lock()
    guarded = once_sink(lambda payload: seen.append(1) or 0)
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        guarded({"x": 1})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with lock:
        assert len(seen) == 1


# ---------------------------------------------------------------------------
# one record per outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_emits_exactly_one_record(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json=completion_body(
                "done",
                {
                    "prompt_tokens": 11,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 4},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            ),
        )

    install_transport(monkeypatch, handler)
    sink = Sink()

    text = await call(sink)

    assert text == "done"
    assert len(sink.payloads) == 1, "a successful call must emit exactly one record"
    rec = sink.payloads[0]
    assert rec["outcome"] == "ok"
    assert rec["error_code"] == ""
    assert rec["attempt"] == 1
    assert rec["purpose"] == "other"
    assert rec["model"] == "gpt-test"
    assert rec["measured"] is True
    assert rec["usage"] == {
        "input_tokens": 11,
        "output_tokens": 5,
        "cached_input_tokens": 4,
        "reasoning_tokens": 2,
    }
    assert rec["duration_ms"] >= 0.0
    assert rec["finished_at"] >= rec["started_at"]


@pytest.mark.asyncio
async def test_no_base_url_emits_exactly_one_record():
    sink = Sink()

    with pytest.raises(LlmCallError):
        await call(sink, base_url=None)

    assert len(sink.payloads) == 1
    assert sink.payloads[0]["outcome"] == "error"
    assert sink.payloads[0]["error_code"] == "no_base_url"
    assert sink.payloads[0]["attempt"] == 1


@pytest.mark.asyncio
async def test_transport_error_emits_exactly_one_record(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    install_transport(monkeypatch, handler)
    sink = Sink()

    with pytest.raises(LlmCallError):
        await call(sink)

    assert len(sink.payloads) == 1
    assert sink.payloads[0]["outcome"] == "error"
    assert sink.payloads[0]["error_code"] == "ConnectError"


@pytest.mark.asyncio
async def test_http_error_status_emits_exactly_one_record(monkeypatch):
    def handler(request):
        return httpx.Response(503, text="upstream is down")

    install_transport(monkeypatch, handler)
    sink = Sink()

    with pytest.raises(LlmCallError):
        await call(sink)

    assert len(sink.payloads) == 1
    assert sink.payloads[0]["outcome"] == "error"
    assert sink.payloads[0]["error_code"] == "http_503"


@pytest.mark.asyncio
async def test_invalid_json_emits_exactly_one_record(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="<html>not json</html>")

    install_transport(monkeypatch, handler)
    sink = Sink()

    with pytest.raises(LlmCallError):
        await call(sink)

    assert len(sink.payloads) == 1
    assert sink.payloads[0]["outcome"] == "error"
    assert sink.payloads[0]["error_code"] == "invalid_json"


@pytest.mark.asyncio
async def test_no_text_emits_exactly_one_record(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"id": "x", "choices": []})

    install_transport(monkeypatch, handler)
    sink = Sink()

    with pytest.raises(LlmCallError):
        await call(sink)

    assert len(sink.payloads) == 1
    assert sink.payloads[0]["outcome"] == "error"
    assert sink.payloads[0]["error_code"] == "no_text"


# ---------------------------------------------------------------------------
# cancellation (the zero-record bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_emits_exactly_one_record(monkeypatch):
    """A call cancelled mid-flight is recorded once as cancelled, then re-raises."""
    in_flight = asyncio.Event()
    release = asyncio.Event()  # never set: the request hangs until cancelled

    async def handler(request):
        in_flight.set()
        await release.wait()
        raise AssertionError("the request must be cancelled before this point")

    install_transport(monkeypatch, handler)
    sink = Sink()

    task = asyncio.ensure_future(call(sink))
    await asyncio.wait_for(in_flight.wait(), timeout=5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(sink.payloads) == 1, (
        "a cancelled call used to emit zero records; it must emit exactly one"
    )
    rec = sink.payloads[0]
    assert rec["outcome"] == "cancelled"
    assert rec["error_code"] == "cancelled"
    assert rec["attempt"] == 1
    assert rec["usage"] is None
    assert rec["measured"] is False


@pytest.mark.asyncio
async def test_cancellation_after_an_emit_still_emits_once(monkeypatch):
    """once_sink wins even if cancellation races a branch that already recorded."""
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        in_flight.set()
        await release.wait()
        raise AssertionError("unreachable")

    install_transport(monkeypatch, handler)
    sink = Sink()
    task = asyncio.ensure_future(call(sink))
    await asyncio.wait_for(in_flight.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # A second cancellation pass over the same call must not add a record.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(sink.payloads) == 1


# ---------------------------------------------------------------------------
# the max_tokens rename retry is ONE logical call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_tokens_rename_retry_emits_one_record_with_attempt_two(monkeypatch):
    seen_payloads: list[dict] = []

    def handler(request):
        body = __import__("json").loads(request.content.decode())
        seen_payloads.append(body)
        if "max_tokens" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Unsupported parameter: 'max_tokens' is not supported",
                        "type": "invalid_request_error",
                        "param": "max_tokens",
                        "code": "unsupported_parameter",
                    }
                },
            )
        assert "max_completion_tokens" in body
        return httpx.Response(200, json=completion_body("renamed"))

    install_transport(monkeypatch, handler)
    sink = Sink()

    text = await call(sink)

    assert text == "renamed"
    assert len(seen_payloads) == 2, "the wire retried once"
    assert len(sink.payloads) == 1, "one logical call, not one record per HTTP post"
    assert sink.payloads[0]["attempt"] == 2
    assert sink.payloads[0]["outcome"] == "ok"


@pytest.mark.asyncio
async def test_second_rejection_after_rename_emits_one_record(monkeypatch):
    """Both failures on the retry path must still collapse into a single record."""

    def handler(request):
        return httpx.Response(
            400,
            json={"error": {"message": "Unsupported parameter: 'max_tokens'",
                            "param": "max_tokens"}},
        )

    install_transport(monkeypatch, handler)
    sink = Sink()

    with pytest.raises(LlmCallError):
        await call(sink)

    assert len(sink.payloads) == 1
    assert sink.payloads[0]["attempt"] == 2
    assert sink.payloads[0]["outcome"] == "error"
    assert sink.payloads[0]["error_code"] == "http_400"


# ---------------------------------------------------------------------------
# a broken sink must never break the call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raising_sink_does_not_break_the_call(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=completion_body("still fine"))

    install_transport(monkeypatch, handler)
    sink = Sink(raises=True)

    assert await call(sink) == "still fine"
    assert len(sink.payloads) == 1


@pytest.mark.asyncio
async def test_raising_sink_does_not_break_a_failed_call(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    install_transport(monkeypatch, handler)
    sink = Sink(raises=True)

    with pytest.raises(LlmCallError):
        await call(sink)

    assert len(sink.payloads) == 1


@pytest.mark.asyncio
async def test_default_recorder_is_used_once_when_no_sink_is_passed(monkeypatch):
    """Calls wired through the process-wide recorder are guarded the same way."""
    sink = Sink()
    set_default_recorder(sink)
    try:
        def handler(request):
            return httpx.Response(200, json=completion_body("via default"))

        install_transport(monkeypatch, handler)
        assert await call(None) == "via default"
    finally:
        set_default_recorder(None)

    assert len(sink.payloads) == 1
