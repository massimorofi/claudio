"""OpenAI-compatible LLM that surfaces ``reasoning_content`` as thought parts.

ADK 2.12's ``OpenAILlm`` reads only ``delta.content`` and ``delta.tool_calls``
from a Chat Completions response. A reasoning model served behind an
OpenAI-compatible endpoint (vLLM, llama.cpp, TGI, ...) delivers its chain of
thought in a separate ``reasoning_content`` field, which the stock integration
drops entirely -- in both the non-streaming response builder and the streaming
generator. The model's thinking therefore never reaches the event stream, and
nothing downstream can persist or restore it.

The Claudio engine runs the ADK in its default non-streaming mode
(``StreamingMode.NONE``), so the response path that matters here is
``_response_to_llm_response``. Two things are done:

1. ``_response_to_llm_response`` is wrapped so a non-streaming response that
   carries ``reasoning_content`` gets it prepended as a
   ``types.Part(text=..., thought=True)``.
2. ``ReasoningOpenAILlm`` overrides ``_generate_content_streaming`` to carry
   ``reasoning_content`` through as thought parts, so the thinking also flows if
   the engine is ever switched to SSE streaming.

Either way the Claudio ``translate_event`` turns thought parts into ``reasoning``
SSE events, and the RunManager persists those to the session JSONL transcript --
so the thinking shows live and is restored on every reload.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Any, AsyncGenerator

import openai
from google.adk.integrations.openai import OpenAILlm
from google.adk.integrations.openai import _openai_llm as _adk_openai
from google.genai import types
from openai import AsyncOpenAI

from claudio_core.accounting import get_default_recorder, once_sink

logger = logging.getLogger(__name__)


def _is_max_tokens_rejection(exc: openai.BadRequestError) -> bool:
    """Whether a 400 is the endpoint rejecting ``max_tokens``.

    The OpenAI SDK exposes the offending field as ``param``; gateways that do
    not fill it in are caught by the message text, which always names both
    the rejected field and the replacement.
    """
    if getattr(exc, "param", None) == "max_tokens":
        return True
    msg = str(exc).lower()
    return "max_tokens" in msg and "max_completion_tokens" in msg


def make_openai_client(
    base_url: str | None,
    api_key: str | None,
    call_context: dict[str, Any] | None = None,
) -> AsyncOpenAI:
    """Build an ``AsyncOpenAI`` client that survives header-stripping gateways.

    Two adaptations for enterprise LLM gateways (e.g. Merck's GPTeal) that
    plain OpenAI-compatible clients do not handle:

    1. **Auth header.** The gateway drops ``Authorization`` and ``x-api-key``
       and only accepts the key in an ``api-key`` header. Standard servers
       ignore the extra header, so sending it is safe everywhere.
    2. **``max_tokens`` rejection.** gpt-5/6-class reasoning models reject
       ``max_tokens`` and require ``max_completion_tokens``. The model name
       alone (``auto``, a gateway alias) is not always enough to know that in
       advance, so ``chat.completions.create`` is wrapped to rename the field
       and retry once when the endpoint rejects it. This covers both the
       streaming and non-streaming paths.

    ``call_context`` (issue #58, Increment 0) attaches unified-call accounting
    to every HTTP call this client makes: ``{"recorder": callable, "run_id",
    "session_id", "purpose", "model_role", "profile_name", "model"}``. The
    recorder is called once per *logical* call (the max_tokens rename is one
    call with ``attempt=2``) with the same payload shape the
    :mod:`claudio_core.llm` usage sink uses. When no recorder is given the
    process-wide default recorder is used, so the daemon agent's calls are
    accounted for too.

    Increment 0a(b)-ia closes the two engine-side coverage holes: the recorder
    is wrapped in :func:`claudio_core.accounting.once_sink` per logical call, so
    no combination of branches (rename retry followed by an error, a stream that
    completed and was then cancelled) can emit twice; and an
    ``asyncio.CancelledError`` -- a ``BaseException``, so invisible to the
    ``except Exception`` nets -- is caught explicitly and emitted once with
    ``outcome="cancelled", error_code="cancelled"`` before being re-raised
    unchanged.
    """
    key = api_key or "local"
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=key,
        default_headers={"api-key": key},
    )
    client._claudio_call_context = dict(call_context or {})  # type: ignore[attr-defined]
    _patch_max_tokens_retry(client)
    return client


def _usage_from_response(result: Any) -> dict[str, int] | None:
    """Map an OpenAI ChatCompletion's usage onto Claudio's accounting names.

    ``reasoning_tokens`` is a *subset* of ``completion_tokens`` (the OpenAI
    wire contract) -- it is kept in its own field so run totals never
    double-count it.
    """
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    try:
        details = getattr(usage, "prompt_tokens_details", None)
        cdetails = getattr(usage, "completion_tokens_details", None)
        return {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "cached_input_tokens": int(
                getattr(details, "cached_tokens", 0) or 0
            ),
            "reasoning_tokens": int(
                getattr(cdetails, "reasoning_tokens", 0) or 0
            ),
        }
    except (TypeError, ValueError):
        return None


def _record_call(
    recorder: Any,
    ctx: dict[str, Any],
    model: str,
    started: float,
    attempt: int,
    outcome: str,
    error_code: str,
    usage: dict[str, int] | None,
) -> None:
    """Fire the accounting recorder defensively -- telemetry never breaks a call."""
    if recorder is None:
        return
    now = time.time()
    try:
        recorder(
            {
                "purpose": ctx.get("purpose") or "primary",
                "model": model or ctx.get("model") or "",
                "run_id": ctx.get("run_id", ""),
                "session_id": ctx.get("session_id", ""),
                "model_role": ctx.get("model_role", ""),
                "profile_name": ctx.get("profile_name", ""),
                "reasoning_effort": ctx.get("reasoning_effort", ""),
                "started_at": started,
                "finished_at": now,
                "duration_ms": (now - started) * 1000.0,
                "outcome": outcome,
                "error_code": error_code,
                "attempt": attempt,
                "usage": usage,
                "measured": usage is not None,
            }
        )
    except Exception:  # noqa: BLE001 - a broken recorder must not fail the call
        logger.debug("llm-call recorder failed", exc_info=True)


def _patch_max_tokens_retry(client: AsyncOpenAI) -> None:
    """Replace ``chat.completions.create`` with a max_tokens-rename retry.

    Also the unified accounting seam for the engine path: every HTTP call this
    client makes -- primary turns, worker sub-agents, the daemon agent --
    flows through this wrapper exactly once per logical call.
    """
    original_create = client.chat.completions.create

    async def create_with_retry(*args: Any, **kwargs: Any):
        ctx = getattr(client, "_claudio_call_context", None) or {}
        # One emission budget per logical call: the max_tokens rename plus a
        # later error, or an error branch racing a cancellation, can then never
        # produce two records for the same call (issue #58, Inc 0a(b)-ia).
        recorder = once_sink(ctx.get("recorder") or get_default_recorder())
        if recorder is None:
            # Fast path: no accounting attached, behave exactly as before.
            try:
                return await original_create(*args, **kwargs)
            except openai.BadRequestError as exc:
                if "max_tokens" not in kwargs or not _is_max_tokens_rejection(exc):
                    raise
                kwargs = dict(kwargs)
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                return await original_create(*args, **kwargs)

        model = str(kwargs.get("model") or "")
        started = time.time()
        attempt = 1
        try:
            try:
                result = await original_create(*args, **kwargs)
            except openai.BadRequestError as exc:
                if "max_tokens" not in kwargs or not _is_max_tokens_rejection(exc):
                    _record_call(
                        recorder, ctx, model, started, attempt,
                        "error", exc.__class__.__name__, None,
                    )
                    # Mark it so the outer handler does not record twice.
                    try:
                        exc._claudio_recorded = True
                    except AttributeError:  # pragma: no cover - exotic exceptions
                        pass
                    raise
                attempt = 2
                kwargs = dict(kwargs)
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                result = await original_create(*args, **kwargs)
        except asyncio.CancelledError:
            # ``CancelledError`` derives from ``BaseException``, so the
            # ``except Exception`` net below never saw it and a run cancelled
            # mid-call emitted *zero* records (issue #58, Inc 0a(b)-ia). Emit
            # exactly one cancelled record -- the once_sink guard makes a
            # second emission structurally impossible -- then re-raise the
            # cancellation completely unchanged.
            _record_call(
                recorder, ctx, model, started, attempt,
                "cancelled", "cancelled", None,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - record, then surface unchanged
            if not getattr(exc, "_claudio_recorded", False):
                _record_call(
                    recorder, ctx, model, started, attempt,
                    "error", exc.__class__.__name__, None,
                )
            raise
        # A streaming call's usage arrives on the final chunk, not on the
        # create() return value. Recording it here would be unmeasured (zero
        # tokens), which would silently defeat the Inc-5 token budget and zero
        # the cache signal whenever streaming is on. So the streaming generator
        # (ReasoningOpenAILlm._generate_content_streaming) records the call once
        # with the real usage when it completes; we skip the create-time record.
        if bool(kwargs.get("stream")):
            return result
        usage = _usage_from_response(result)
        _record_call(recorder, ctx, model, started, attempt, "ok", "", usage)
        return result

    client.chat.completions.create = create_with_retry  # type: ignore[method-assign]


# Capture the upstream function BEFORE patching, so the wrapper calls the real
# implementation rather than recursing into itself once the module attribute is
# replaced.
_ORIGINAL_RESPONSE_TO_LLM_RESPONSE = _adk_openai._response_to_llm_response


def _reasoning_of(response: Any) -> str:
    """Extract ``reasoning_content`` from a Chat Completion, if present."""
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    reasoning = getattr(message, "reasoning_content", None)
    return reasoning if isinstance(reasoning, str) else ""


def _response_with_reasoning(response: Any):
    """Build an LlmResponse that carries reasoning as a leading thought part.

    Wraps the upstream ``_response_to_llm_response``: the base response is
    produced unchanged, then any ``reasoning_content`` is prepended as a
    ``thought`` part so the downstream event translator emits it as reasoning.
    Responses without reasoning (the common case for non-reasoning models) pass
    through byte-for-byte identical.
    """
    llm_response = _ORIGINAL_RESPONSE_TO_LLM_RESPONSE(response)
    reasoning = _reasoning_of(response)
    if not reasoning:
        return llm_response
    existing = (
        list(llm_response.content.parts)
        if llm_response.content and llm_response.content.parts
        else []
    )
    parts = [types.Part(text=reasoning, thought=True), *existing]
    llm_response.content = types.Content(role="model", parts=parts)
    return llm_response


# The non-streaming path calls this module-level function by name at call time,
# so replacing the module attribute is what actually takes effect for it. This
# is applied once, on import, and is idempotent.
if getattr(_adk_openai._response_to_llm_response, "_claudio_reasoning", False) is False:
    _response_with_reasoning._claudio_reasoning = True
    _adk_openai._response_to_llm_response = _response_with_reasoning


class ReasoningOpenAILlm(OpenAILlm):
    """``OpenAILlm`` that forwards ``reasoning_content`` as thought parts."""

    async def _generate_content_streaming(
        self, kwargs: dict[str, Any]
    ) -> AsyncGenerator[Any, None]:
        kwargs["stream"] = True
        # Ask for the trailing usage-only chunk, same as the upstream path.
        kwargs["stream_options"] = {"include_usage": True}
        _t0 = time.time()
        raw_stream = await self._openai_client.chat.completions.create(**kwargs)

        ctx = getattr(self._openai_client, "_claudio_call_context", None) or {}
        # Shared by the completion record and the cancellation branch below, so
        # this one logical stream emits at most one record (issue #58,
        # Inc 0a(b)-ia): a stream that finished and was *then* cancelled must
        # not be counted twice.
        recorder = once_sink(ctx.get("recorder") or get_default_recorder())

        def _record_streamed(
            usage_obj: Any, outcome: str = "ok", error_code: str = ""
        ) -> None:
            # Record the streaming call once, with the real usage from the
            # trailing chunk, so the token budget and cache signal work under
            # streaming (issue #58 review). create_with_retry skips its own
            # record for stream=True to avoid double-counting.
            if recorder is None:
                return
            _record_call(
                recorder, ctx, str(kwargs.get("model") or ""), _t0, 1,
                outcome, error_code,
                _usage_from_response(SimpleNamespace(usage=usage_obj))
                if outcome == "ok" else None,
            )

        text_accumulated = ""
        reasoning_accumulated = ""
        tool_calls_accumulated: dict[int, dict[str, Any]] = {}
        usage = None
        finish_reason = None

        stream_iter = raw_stream.__aiter__()
        while True:
            try:
                chunk = await stream_iter.__anext__()
            except StopAsyncIteration:
                break
            except asyncio.CancelledError:
                # The run was cancelled while the stream was being consumed:
                # the call is real, in-flight and unrecorded, so emit exactly
                # one cancelled record before the cancellation propagates.
                _record_streamed(None, "cancelled", "cancelled")
                raise
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = choice.delta

            # Chain-of-thought: surface each reasoning delta as a thought part so
            # it streams live and lands in the final aggregated event.
            reasoning_delta = getattr(delta, "reasoning_content", None)
            if reasoning_delta:
                reasoning_accumulated += reasoning_delta
                yield _adk_openai.LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=reasoning_delta, thought=True)],
                    ),
                    partial=True,
                )

            if delta.content:
                text_accumulated += delta.content
                yield _adk_openai.LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=delta.content)],
                    ),
                    partial=True,
                )

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    index = tc_delta.index
                    if index not in tool_calls_accumulated:
                        tool_calls_accumulated[index] = {
                            "id": tc_delta.id,
                            "name": (
                                tc_delta.function.name if tc_delta.function else None
                            ),
                            "arguments": "",
                        }
                    else:
                        if tc_delta.id:
                            tool_calls_accumulated[index]["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            tool_calls_accumulated[index]["name"] = (
                                tc_delta.function.name
                            )

                    arguments_delta = (
                        tc_delta.function.arguments if tc_delta.function else None
                    )
                    partial_args = None
                    if arguments_delta:
                        tool_calls_accumulated[index]["arguments"] += arguments_delta
                        tracker = tool_calls_accumulated[index].setdefault(
                            "tracker",
                            _adk_openai.streaming_utils._JsonPathTracker(),
                        )
                        partial_args = tracker.handle_chunk(arguments_delta)

                    yield _adk_openai.LlmResponse(
                        partial=True,
                        content=types.Content(
                            role="model",
                            parts=[
                                types.Part(
                                    function_call=types.FunctionCall(
                                        id=tool_calls_accumulated[index]["id"],
                                        name=(
                                            tool_calls_accumulated[index]["name"]
                                            or None
                                        ),
                                        partial_args=partial_args or None,
                                        will_continue=True,
                                    )
                                )
                            ],
                        ),
                    )

        # Final, non-partial response: reasoning first (as a thought part), then
        # the answer text, then any tool calls -- the order the Claudio event
        # translator expects.
        parts: list[types.Part] = []
        if reasoning_accumulated:
            parts.append(types.Part(text=reasoning_accumulated, thought=True))
        if text_accumulated:
            parts.append(types.Part.from_text(text=text_accumulated))

        for index in sorted(tool_calls_accumulated.keys()):
            acc = tool_calls_accumulated[index]
            args: dict[str, Any] = {}
            if acc["arguments"]:
                try:
                    args = json.loads(acc["arguments"])
                except json.JSONDecodeError:
                    logger.warning(
                        "Failed to parse accumulated tool call arguments as JSON."
                    )
            part = types.Part.from_function_call(name=acc["name"], args=args)
            part.function_call.id = acc["id"]
            parts.append(part)

        mapped_finish_reason = _adk_openai._map_finish_reason(finish_reason)
        _record_streamed(usage)

        if not parts and mapped_finish_reason not in (
            None,
            types.FinishReason.STOP,
        ):
            yield _adk_openai.LlmResponse(
                error_code=mapped_finish_reason,
                error_message=(
                    "OpenAI streaming response finished with reason"
                    f" {finish_reason!r} and no content."
                ),
                finish_reason=mapped_finish_reason,
                usage_metadata=_adk_openai._usage_metadata(usage),
            )
            return

        yield _adk_openai.LlmResponse(
            content=types.Content(role="model", parts=parts),
            partial=False,
            usage_metadata=_adk_openai._usage_metadata(usage),
            finish_reason=mapped_finish_reason,
        )


__all__ = ["ReasoningOpenAILlm", "make_openai_client"]
