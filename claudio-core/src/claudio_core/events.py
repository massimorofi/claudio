"""Claudio's SSE event protocol — the frozen wire contract.

Every event the backend pushes to the frontend is defined here: the event name,
the payload shape, and how to build it. Nothing in this module knows or cares
which agent framework produced the data. That is the point.

The protocol is frozen deliberately. Both the ADK engine (``claudio-backend``)
and the React frontend (``claudio-frontend``) depend on it, and
``frontend/src/api/types.ts`` is its TypeScript mirror. Changing anything here
means changing the mirror too, and the contract tests in
``tests/test_event_protocol.py`` exist to catch drift between the two.

Wire format
-----------
Each SSE frame carries::

    id: <monotonic sequence number>
    event: <one of EventName>
    data: <JSON object>

``data`` is always a JSON *object*, never a bare scalar, so a consumer can
always ``JSON.parse`` and index into it.

Two rules that are easy to get wrong and are load-bearing:

1. ``tool_call.arguments`` and ``approval_required.arguments`` MUST be JSON
   **strings**, not objects. The frontend pretty-prints them by parsing, and if
   an object arrives React tries to render it as a child and crashes the view.
2. ``tool_result.output`` MUST be a string for the same reason.

Both are enforced by :func:`encode_event` rather than left to each producer.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Final, TypedDict


class EventName(str, Enum):
    """Every SSE event name Claudio emits.

    Values are the literal strings that go on the wire; they are frozen and must
    match ``frontend/src/api/types.ts``.
    """

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_RESTARTING = "run_restarting"
    RUN_COMPACTED = "run_compacted"

    TOKEN = "token"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    HANDOFF = "handoff"

    APPROVAL_REQUIRED = "approval_required"
    CONTEXT_USAGE = "context_usage"
    HEARTBEAT = "heartbeat"


#: Every event name as a plain string set, for validation.
ALL_EVENT_NAMES: Final[frozenset[str]] = frozenset(e.value for e in EventName)


class TokenEventData(TypedDict):
    text: str


class ReasoningEventData(TypedDict):
    text: str


class ToolCallEventData(TypedDict):
    call_id: str
    name: str
    arguments: str  # JSON string, never a dict — see module docstring


class ToolResultEventData(TypedDict):
    call_id: str
    name: str
    output: str  # stringified, never a raw object
    is_error: bool


class HandoffEventData(TypedDict, total=False):
    from_agent: str | None
    to_agent: str | None


class ApprovalRequiredEventData(TypedDict):
    approval_id: str
    tool: str
    arguments: str  # JSON string, never a dict
    reason: str


class RunStartedEventData(TypedDict):
    run_id: str
    session_id: str


class UsageInfo(TypedDict):
    input_tokens: int
    output_tokens: int
    cost_usd: float


class RunCompletedEventData(TypedDict):
    run_id: str
    session_id: str
    final_output: str
    usage: UsageInfo
    summary: dict[str, Any]


class ErrorInfo(TypedDict):
    code: str
    message: str
    retryable: bool


class RunFailedEventData(TypedDict):
    run_id: str
    error: ErrorInfo


class RunRestartingEventData(TypedDict):
    reason: str  # an error code, or "fallback"
    attempt: int


class RunCompactedEventData(TypedDict):
    reason: str  # "manual" | "threshold" | "overflow"
    attempt: int


class ContextUsageEventData(TypedDict):
    used_tokens: int
    context_window: int
    usable_tokens: int
    free_tokens: int
    percent_used: float
    compact_threshold: int
    source: str  # "model" | "estimate" | "stored"
    auto_disabled: bool


#: Events whose payload must be a non-empty string in ``arguments``.
_STRINGIFIED_ARGUMENT_EVENTS: Final[frozenset[str]] = frozenset(
    {EventName.TOOL_CALL.value, EventName.APPROVAL_REQUIRED.value}
)


def as_json_string(value: Any) -> str:
    """Serialise a tool-call argument mapping to the JSON string the wire requires.

    A value that is already a string is passed through untouched: the model
    produces JSON text, and re-encoding it would double-escape it.
    """
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def stringify_output(value: Any) -> str:
    """Serialise a tool result to the string the wire requires."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def is_error_output(output: Any) -> bool:
    """Heuristic: did this tool result represent a failure?

    Claudio's tools return ``{"ok": False, ...}`` on handled failures; a raw
    string starting with "Error" or carrying the ``<tool_use_error>`` marker is
    an unhandled one.
    """
    if isinstance(output, dict):
        return bool(output.get("ok") is False or output.get("is_error"))
    if isinstance(output, str):
        return output.startswith("Error") or "<tool_use_error>" in output
    return False


def encode_event(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise one event for the wire.

    Returns ``{"event": name, "data": {...}}`` with the stringification rules
    applied. Raises :class:`ValueError` for an unknown event name or for a
    payload that violates the contract, so a producer bug surfaces loudly at
    the seam instead of as a blank screen in the browser.
    """
    if name not in ALL_EVENT_NAMES:
        raise ValueError(f"unknown SSE event name: {name!r}")
    if not isinstance(data, dict):
        raise ValueError(f"SSE data must be a JSON object, got {type(data).__name__}")

    if name in _STRINGIFIED_ARGUMENT_EVENTS:
        arguments = data.get("arguments")
        if not isinstance(arguments, str):
            data = {**data, "arguments": as_json_string(arguments)}
        # A malformed string is still better than an object, but flag it.
        try:
            json.loads(data["arguments"])
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"{name} arguments must be valid JSON text: {exc}"
            ) from exc

    if name == EventName.TOOL_RESULT.value:
        output = data.get("output")
        if not isinstance(output, str):
            data = {**data, "output": stringify_output(output)}

    return {"event": name, "data": data}


def format_sse(name: str, data: dict[str, Any], seq: int | None = None) -> str:
    """Render one ``text/event-stream`` frame.

    Multi-line data is handled by the JSON encoding (which escapes newlines), so
    a single ``data:`` line is always sufficient.
    """
    payload = encode_event(name, data)
    lines: list[str] = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {payload['event']}")
    lines.append(f"data: {json.dumps(payload['data'], default=str)}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "EventName",
    "ALL_EVENT_NAMES",
    "TokenEventData",
    "ReasoningEventData",
    "ToolCallEventData",
    "ToolResultEventData",
    "HandoffEventData",
    "ApprovalRequiredEventData",
    "RunStartedEventData",
    "RunCompletedEventData",
    "RunFailedEventData",
    "RunRestartingEventData",
    "RunCompactedEventData",
    "ContextUsageEventData",
    "UsageInfo",
    "ErrorInfo",
    "as_json_string",
    "stringify_output",
    "is_error_output",
    "encode_event",
    "format_sse",
]
