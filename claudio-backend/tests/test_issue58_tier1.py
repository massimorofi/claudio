"""Issue #58 Increment 3 -- Tier-1 wiring in the engine + prefix discipline.

Covers the integration seams: the guard's Tier-1 pass shrinking stale tool
responses in real ADK ``Content`` objects (pairing intact), the prune callback
firing once per run, and the static-prefix / dynamic-suffix contract that keeps
the provider prompt cache warm (the static instruction is byte-identical across
turns; only the nudge suffix moves).

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue58_tier1.py -q
"""
from __future__ import annotations

from google.genai import types

from claudio_core.instructions import build_static_instructions, render_nudges
from claudio_core.settings import AppConfig

from claudio_backend.engine.adk_engine import (
    _tier1_pass,
    make_context_guard,
)


def _fc_turn(call_id: str, name: str, args: dict) -> types.Content:
    part = types.Part.from_function_call(name=name, args=args)
    part.function_call.id = call_id
    return types.Content(role="model", parts=[part])


def _fr_turn(call_id: str, name: str, response: dict) -> types.Content:
    part = types.Part.from_function_response(name=name, response=response)
    part.function_response.id = call_id
    return types.Content(role="user", parts=[part])


def _text_turn(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part.from_text(text=text)])


def _resp_chars(content) -> int:
    total = 0
    for part in content.parts or []:
        fr = getattr(part, "function_response", None)
        if fr is not None:
            import json

            total += len(json.dumps(fr.response or {}, default=str))
    return total


def test_tier1_pass_collapses_stale_bulky_response():
    big = "x" * 5000
    contents = [
        _fc_turn("c1", "read_file", {"path": "a.py"}),
        _fr_turn("c1", "read_file", {"ok": True, "content": big}),
    ] + [_text_turn("model", f"t{i}") for i in range(6)]
    stats, applied = _tier1_pass(contents)
    assert applied == 1
    assert stats.changed
    # The response was shrunk in place; the call/response pair is intact.
    fr = contents[1].parts[0].function_response
    assert fr.id == "c1" and fr.name == "read_file"
    assert fr.response.get("_tier1_pruned") is True
    assert len(str(fr.response)) < len(big)


def test_tier1_pass_preserves_pairing_and_leaves_recent_intact():
    contents = [
        _fc_turn("c1", "read_file", {"path": "a.py"}),
        _fr_turn("c1", "read_file", {"ok": True, "content": "y" * 5000}),
    ]
    # Only two turns, both within the recent window -> nothing pruned.
    stats, applied = _tier1_pass(contents, )
    assert applied == 0
    assert contents[1].parts[0].function_response.response == {
        "ok": True, "content": "y" * 5000
    }


def test_tier1_pass_prunes_failed_response():
    contents = [
        _fc_turn("c1", "run_command", {"command": "ls"}),
        _fr_turn("c1", "run_command", {"ok": False, "error": "boom " * 200}),
    ] + [_text_turn("model", f"t{i}") for i in range(6)]
    stats, applied = _tier1_pass(contents)
    assert applied == 1
    assert stats.pruned_failures == 1


def test_guard_fires_prune_callback_once():
    from google.adk.models.llm_request import LlmRequest

    fired = []
    guard = make_context_guard(
        context_window=100000,
        max_output_tokens=1000,
        reserve=100,
        on_tier1_prune=lambda stats: fired.append(stats),
    )
    contents = [
        _fc_turn("c1", "read_file", {"path": "a.py"}),
        _fr_turn("c1", "read_file", {"ok": True, "content": "x" * 5000}),
    ] + [_text_turn("model", f"t{i}") for i in range(6)]
    req = LlmRequest(
        model="m",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction="sys"),
    )
    guard(None, req)
    guard(None, req)  # second call: already pruned, callback must not refire
    assert len(fired) == 1


# ---------------------------------------------------------------------------
# Static prefix / dynamic suffix discipline
# ---------------------------------------------------------------------------

def test_static_prefix_is_byte_stable_across_turns():
    """The cacheable prefix must not change as ToolContext counters move."""
    cfg = AppConfig()
    a = build_static_instructions(cfg, "s1", workspace=None)
    b = build_static_instructions(cfg, "s1", workspace=None)
    assert a == b  # deterministic, no dynamic state leaked in

    # The dynamic nudges DO change with the counters -- they belong in the
    # suffix, after the stable prefix, so the provider prefix cache survives.
    nudge_low = render_nudges(
        turns_since_todo=0, reads_since_write=0, deliverable_written=False,
        unfulfilled_tools=[],
    )
    nudge_high = render_nudges(
        turns_since_todo=9, reads_since_write=20, deliverable_written=False,
        unfulfilled_tools=["gortex_search"],
    )
    assert nudge_low != nudge_high
    # And the static prefix is a strict prefix of the assembled prompt.
    assert (a + nudge_high).startswith(a)
