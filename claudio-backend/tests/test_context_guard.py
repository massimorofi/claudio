"""The per-request context guard: the always-on overflow prevention.

The summary compactor only sees the *previous* turn's reported size, so a
single large tool result can push the next request past the model's hard
ceiling before any check fires. The context guard closes that gap by
truncating the request contents immediately before every model call. These
tests drive the guard directly against real ADK ``LlmRequest`` / ``Content``
objects and assert it (a) trims oversized requests to fit, (b) leaves
in-range requests untouched, and (c) never leaves the kept window starting
on an orphaned tool response.
"""
from __future__ import annotations

from google.genai import types

from claudio_backend.engine.adk_engine import (
    _GUARD_MIN_KEEP_TURNS,
    _classify_engine_error,
    make_context_guard,
)


def _text_turn(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part.from_text(text=text)])


def _fc_turn(name: str, args: dict) -> types.Content:
    return types.Content(
        role="model",
        parts=[types.Part.from_function_call(name=name, args=args)],
    )


def _fr_turn(name: str, response: dict) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part.from_function_response(name=name, response=response)
        ],
    )


def _request(contents, system_instruction="You are a helpful agent."):
    from google.adk.models.llm_request import LlmRequest

    return LlmRequest(
        model="test-model",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system_instruction),
    )


def _turn_chars(content) -> int:
    total = 0
    for part in content.parts or []:
        if getattr(part, "text", None):
            total += len(part.text)
    return total


def test_under_budget_request_is_unchanged():
    contents = [_text_turn("user", "hello"), _text_turn("model", "hi")]
    req = _request(contents)
    guard = make_context_guard(context_window=100_000, max_output_tokens=1024, reserve=1024)
    out = guard(None, req)
    assert out is None  # proceed, do not short-circuit
    assert len(req.contents) == 2


def test_oversized_request_is_trimmed_to_fit():
    # 40 turns of ~1000 chars each; budget is tiny so most must be dropped.
    contents = [
        _text_turn("user" if i % 2 == 0 else "model", f"turn {i} " + "x" * 1000)
        for i in range(40)
    ]
    req = _request(contents)
    # context 8000 - response 1024 - fixed(system ~ small) leaves a few turns.
    guard = make_context_guard(context_window=8000, max_output_tokens=1024, reserve=1024)
    guard(None, req)
    kept = req.contents
    assert len(kept) < 40
    assert len(kept) >= _GUARD_MIN_KEEP_TURNS
    # The kept tail is the most recent turns (last original turn preserved).
    assert kept[-1].parts[0].text.startswith("turn 39")


def test_kept_window_never_starts_on_orphaned_tool_response():
    # A long head ending in a function_call whose response would be orphaned
    # at the front of the kept window.
    contents = [
        _text_turn("user", "start " + "y" * 2000),
        _fc_turn("read_file", {"path": "/a"}),
        _fr_turn("read_file", {"result": "data " + "z" * 2000}),
        _text_turn("user", "next " + "w" * 2000),
        _fc_turn("run_command", {"cmd": "ls"}),
        _fr_turn("run_command", {"out": "files " + "v" * 2000}),
        _text_turn("user", "final question"),
    ]
    req = _request(contents)
    guard = make_context_guard(context_window=4000, max_output_tokens=1024, reserve=1024)
    guard(None, req)
    kept = req.contents
    assert kept, "guard must keep at least one turn"
    # The first kept turn must NOT be a function_response (orphaned call).
    first = kept[0]
    assert not any(
        getattr(p, "function_response", None) is not None for p in (first.parts or [])
    )


def test_guard_passes_through_on_internal_error():
    # If reading the request blows up, the guard must swallow it and pass the
    # request through (return None) rather than crash the run -- a broken
    # guard is worse than no guard.
    class _BoomRequest:
        @property
        def contents(self):
            raise RuntimeError("cannot read contents")

    guard = make_context_guard(context_window=1000, max_output_tokens=512, reserve=512)
    # Must not raise; returns None so the request proceeds unchanged.
    out = guard(None, _BoomRequest())
    assert out is None


def test_fixed_overhead_reduces_contents_budget():
    # A large system instruction eats into the contents budget: with a big
    # instruction the kept contents must be smaller than with a tiny one.
    contents = [
        _text_turn("user" if i % 2 == 0 else "model", f"turn {i} " + "x" * 1000)
        for i in range(30)
    ]
    big_sys = "SYSTEM " + "s" * 40000
    req_big = _request(list(contents), system_instruction=big_sys)
    guard_big = make_context_guard(context_window=20000, max_output_tokens=1024, reserve=1024)
    guard_big(None, req_big)

    req_small = _request(list(contents), system_instruction="tiny")
    guard_small = make_context_guard(context_window=20000, max_output_tokens=1024, reserve=1024)
    guard_small(None, req_small)

    assert len(req_big.contents) < len(req_small.contents)


def test_halogen_overflow_error_classifies_as_context_overflow():
    # The exact wording the halogen server returns when the prompt exceeds
    # its window. This is the message that previously fell through to a
    # generic engine_error and killed the run instead of triggering overflow
    # recovery.
    halogen = RuntimeError(
        "Error code: 400 - {'error': {'message': 'the prompt does not fit: "
        "it is 266162 tokens and the context is 262144, 4018 tokens over. "
        "Shorten the prompt, or raise the context (HALOGEN_CTX, and "
        "HALOGEN_ROPE_YARN for more than the native 262144).', "
        "'type': 'invalid_request_error', 'param': None, 'code': None}}"
    )
    code, retryable = _classify_engine_error(halogen)
    assert code == "context_overflow"
    assert retryable is False


def test_auth_error_still_classifies_as_auth():
    err = RuntimeError("Error code: 401 - invalid api key")
    code, _ = _classify_engine_error(err)
    assert code == "llm_auth_error"


def test_unrelated_error_is_generic_engine_error():
    err = RuntimeError("something went wrong with the tool")
    code, retryable = _classify_engine_error(err)
    assert code == "engine_error"
    assert retryable is True
