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

import json
import logging
from typing import Any, AsyncGenerator

from google.adk.integrations.openai import OpenAILlm
from google.adk.integrations.openai import _openai_llm as _adk_openai
from google.genai import types

logger = logging.getLogger(__name__)


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
        raw_stream = await self._openai_client.chat.completions.create(**kwargs)

        text_accumulated = ""
        reasoning_accumulated = ""
        tool_calls_accumulated: dict[int, dict[str, Any]] = {}
        usage = None
        finish_reason = None

        async for chunk in raw_stream:
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


__all__ = ["ReasoningOpenAILlm"]
