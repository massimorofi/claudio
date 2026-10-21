"""Contract tests for the frozen SSE event protocol.

These are the most important tests in the repo. The wire contract is shared
with ``frontend/src/api/types.ts``; if a producer here drifts from what the
browser expects, the failure shows up as a blank screen, not an exception. So
the invariants are asserted explicitly here, and the TypeScript mirror is
checked against them by reading the file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claudio_core.events import (
    ALL_EVENT_NAMES,
    EventName,
    as_json_string,
    encode_event,
    format_sse,
    is_error_output,
    stringify_output,
)

FRONTEND_TYPES = (
    Path(__file__).resolve().parents[2] / "claudio-frontend" / "src" / "api" / "types.ts"
)


def test_every_event_name_is_a_lowercase_snake_string():
    for name in ALL_EVENT_NAMES:
        assert isinstance(name, str)
        assert name == name.lower()
        assert name.replace("_", "").isalpha()


def test_all_documented_events_are_present():
    expected = {
        "token",
        "reasoning",
        "tool_call",
        "tool_result",
        "handoff",
        "approval_required",
        "run_started",
        "run_completed",
        "run_failed",
        "run_restarting",
        "run_compacted",
        "context_usage",
        "heartbeat",
    }
    assert ALL_EVENT_NAMES == expected


def test_encode_rejects_unknown_event():
    with pytest.raises(ValueError, match="unknown SSE event"):
        encode_event("not_a_real_event", {"x": 1})


def test_encode_rejects_non_object_payload():
    with pytest.raises(ValueError, match="must be a JSON object"):
        encode_event("token", "just a string")


def test_tool_call_arguments_dict_is_serialised_to_string():
    evt = encode_event(
        "tool_call",
        {"call_id": "c1", "name": "write_file", "arguments": {"path": "a.txt"}},
    )
    assert isinstance(evt["data"]["arguments"], str)
    assert json.loads(evt["data"]["arguments"]) == {"path": "a.txt"}


def test_tool_call_arguments_already_string_pass_through():
    raw = json.dumps({"path": "a.txt"})
    evt = encode_event("tool_call", {"call_id": "c1", "name": "f", "arguments": raw})
    assert evt["data"]["arguments"] == raw


def test_tool_call_arguments_must_be_valid_json_text():
    with pytest.raises(ValueError, match="valid JSON"):
        encode_event("tool_call", {"call_id": "c1", "name": "f", "arguments": "{broken"})


def test_approval_required_arguments_serialised():
    evt = encode_event(
        "approval_required",
        {
            "approval_id": "a1",
            "tool": "run_command",
            "arguments": {"command": "ls"},
            "reason": "r",
        },
    )
    assert json.loads(evt["data"]["arguments"]) == {"command": "ls"}


def test_tool_result_output_coerced_to_string():
    evt = encode_event(
        "tool_result",
        {"call_id": "c1", "name": "f", "output": {"ok": True}, "is_error": False},
    )
    assert isinstance(evt["data"]["output"], str)
    assert json.loads(evt["data"]["output"]) == {"ok": True}


def test_tool_result_none_output_becomes_empty_string():
    evt = encode_event("tool_result", {"call_id": "c1", "name": "f", "output": None})
    assert evt["data"]["output"] == ""


def test_as_json_string_handles_none_and_passthrough():
    assert as_json_string(None) == "{}"
    assert as_json_string('{"a": 1}') == '{"a": 1}'
    assert json.loads(as_json_string({"a": 1})) == {"a": 1}


def test_stringify_output_never_returns_non_string():
    for value in [None, 123, ["a"], {"b": 2}, "text"]:
        assert isinstance(stringify_output(value), str)


def test_is_error_output_recognises_handled_failures():
    assert is_error_output({"ok": False, "error": "boom"}) is True
    assert is_error_output({"ok": True}) is False
    assert is_error_output("Error: something broke") is True
    assert is_error_output("<tool_use_error>bad</tool_use_error>") is True
    assert is_error_output("all good") is False


def test_format_sse_frame_shape():
    frame = format_sse("token", {"text": "hi"}, seq=7)
    assert frame.startswith("id: 7\nevent: token\ndata: ")
    assert frame.endswith("\n\n")
    lines = frame.split("\n")
    assert json.loads(lines[2][len("data: ") :]) == {"text": "hi"}


def test_format_sse_escapes_newlines_in_payload():
    # A newline inside the payload must not break the frame into extra lines.
    frame = format_sse("token", {"text": "line1\nline2"}, seq=1)
    assert frame.count("\n") == 4  # id, event, data, blank, blank
    data_line = frame.split("\n")[2]
    assert json.loads(data_line[len("data: ") :])["text"] == "line1\nline2"


def test_event_name_enum_values_match_strings():
    assert EventName.TOKEN.value == "token"
    assert EventName.RUN_COMPLETED.value == "run_completed"
    assert EventName("token") is EventName.TOKEN


#: Core event name -> the TypeScript interface that describes its payload.
TS_INTERFACE_FOR = {
    "token": "TokenEvent",
    "reasoning": "ReasoningEvent",
    "tool_call": "ToolCallEvent",
    "tool_result": "ToolResultEvent",
    "approval_required": "ApprovalRequiredEvent",
    "run_completed": "RunCompletedEvent",
    "run_failed": "RunFailedEvent",
    "run_restarting": "RunRestartingEvent",
    "context_usage": "ContextUsage",
}


@pytest.mark.skipif(not FRONTEND_TYPES.exists(), reason="frontend not present yet")
def test_typescript_mirror_declares_every_event_payload():
    """Each event's payload must have a matching interface in the TS mirror."""
    ts = FRONTEND_TYPES.read_text()
    for event_name, iface in TS_INTERFACE_FOR.items():
        assert f"interface {iface}" in ts, (
            f"{event_name}: expected `interface {iface}` in {FRONTEND_TYPES}"
        )


@pytest.mark.skipif(not FRONTEND_TYPES.exists(), reason="frontend not present yet")
def test_typescript_tool_call_arguments_is_string():
    """The load-bearing invariant: arguments/output are strings in TS too."""
    ts = FRONTEND_TYPES.read_text()
    tool_call = ts.split("interface ToolCallEvent")[1].split("}")[0]
    assert "arguments: string" in tool_call
    tool_result = ts.split("interface ToolResultEvent")[1].split("}")[0]
    assert "output: string" in tool_result
