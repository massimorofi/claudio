"""Translate OpenAI Agents SDK stream events into Claudio's SSE event protocol.

This is the seam between the SDK's event shapes and our wire protocol. If the SDK's
events change, only this file changes.
"""
from __future__ import annotations

from typing import Any

from agents import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)


def _raw_event_type(event: RawResponsesStreamEvent) -> str:
    """The Responses-API event type string (e.g. response.output_text.delta)."""
    return getattr(event.data, "type", "") or type(event.data).__name__


def translate_stream_event(event) -> list[dict[str, Any]]:
    """Map one SDK stream event to zero or more Claudio SSE events.

    Each returned dict is ``{"event": <name>, "data": {...}}``.
    """
    out: list[dict[str, Any]] = []

    if isinstance(event, RawResponsesStreamEvent):
        raw_type = _raw_event_type(event)
        if raw_type == "response.output_text.delta":
            delta = getattr(event.data, "delta", "")
            if delta:
                out.append({"event": "token", "data": {"text": delta}})
        elif raw_type == "response.reasoning_summary_text.delta":
            delta = getattr(event.data, "delta", "")
            if delta:
                out.append({"event": "reasoning", "data": {"text": delta}})
        return out

    if isinstance(event, RunItemStreamEvent):
        name = event.name
        if name == "tool_called":
            item = event.item
            out.append(
                {
                    "event": "tool_call",
                    "data": {
                        "call_id": getattr(item, "call_id", None),
                        "name": getattr(item, "tool_name", None)
                        or getattr(item, "name", None),
                        "arguments": _safe_args(getattr(item, "raw_input", None)),
                    },
                }
            )
        elif name == "tool_output":
            item = event.item
            output = getattr(item, "output", None)
            is_error = _is_error_output(output)
            out.append(
                {
                    "event": "tool_result",
                    "data": {
                        "call_id": getattr(item, "call_id", None),
                        "name": getattr(item, "tool_name", None),
                        "output": _stringify_output(output),
                        "is_error": is_error,
                    },
                }
            )
        elif name in ("handoff_requested", "handoff_occured"):
            item = event.item
            out.append(
                {
                    "event": "handoff",
                    "data": {
                        "from_agent": getattr(item, "source_name", None),
                        "to_agent": getattr(item, "target_name", None),
                    },
                }
            )
        # message_output_created / reasoning_item_created: text already streamed
        # via raw deltas; nothing extra to emit.
        return out

    if isinstance(event, AgentUpdatedStreamEvent):
        out.append(
            {
                "event": "handoff",
                "data": {"to_agent": getattr(event.new_agent, "name", None)},
            }
        )
        return out

    return out


def _safe_args(raw: Any) -> str:
    """Return the tool-call arguments as a JSON string.

    The frontend types ``arguments`` as a string and pretty-prints it by parsing,
    so the wire format must be a string — sending a dict makes React try to render
    an object as a child and crash the view.
    """
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        return raw
    import json

    try:
        return json.dumps(raw)
    except (TypeError, ValueError):
        return str(raw)


def _is_error_output(output: Any) -> bool:
    if isinstance(output, dict):
        return bool(output.get("ok") is False or output.get("is_error"))
    if isinstance(output, str):
        return output.startswith("Error") or "<tool_use_error>" in output
    return False


def _stringify_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    import json

    try:
        return json.dumps(output, default=str)
    except (TypeError, ValueError):
        return str(output)
