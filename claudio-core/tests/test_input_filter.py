"""Unit tests for the preventive input-sanitization layer (``agent/recovery.py``).

These are pure-function tests: no model, no DB. They pin the contract of the
``call_model_input_filter`` (repair truncated calls, close dangling calls,
truncate over-long history, and request compaction before truncation) and the
``tool_error_formatter``
(tool-not-found → actionable guidance).
"""
from __future__ import annotations

import json

import pytest
from claudio_core.recovery import CallModelData, ModelInputData, ToolErrorFormatterArgs

import claudio_core.recovery as r


def _call(items, instructions="do things"):
    md = ModelInputData(input=items, instructions=instructions)
    return CallModelData(model_data=md)


# ---- repair truncated tool-call arguments -----------------------------


def test_repairs_truncated_arguments():
    bad = {"type": "function_call", "call_id": "c1", "name": "write_file",
           "arguments": '{"path": "/x", "body": "unterminated'}
    out = r.make_input_filter(100000)(_call([bad]))
    call = next(i for i in out.input if i.get("type") == "function_call")
    # The invalid arguments are replaced with valid empty-object JSON.
    assert call["arguments"] == "{}"
    assert json.loads(call["arguments"]) == {}
    # A paired output explaining the truncation was inserted for the call.
    outputs = [i for i in out.input if i.get("type") == "function_call_output"]
    assert any(o.get("call_id") == "c1" for o in outputs)


def test_valid_arguments_left_untouched():
    good = {"type": "function_call", "call_id": "c1", "name": "read_file",
           "arguments": '{"path": "/x"}'}
    out = r.make_input_filter(100000)(_call([good]))
    call = next(i for i in out.input if i.get("type") == "function_call")
    assert call["arguments"] == '{"path": "/x"}'


# ---- close dangling calls ---------------------------------------------


def test_closes_dangling_calls():
    fc = {"type": "function_call", "call_id": "cX", "name": "run_command",
          "arguments": "{}"}
    out = r.make_input_filter(100000)(_call([fc]))
    # The call now has a matching output so the template never sees an orphan.
    outputs = [i for i in out.input if i.get("type") == "function_call_output"]
    assert any(o.get("call_id") == "cX" for o in outputs)


def test_does_not_duplicate_existing_output():
    fc = {"type": "function_call", "call_id": "c1", "name": "x", "arguments": "{}"}
    fco = {"type": "function_call_output", "call_id": "c1", "output": "ok"}
    out = r.make_input_filter(100000)(_call([fc, fco]))
    outs = [i for i in out.input if i.get("type") == "function_call_output"
            and i.get("call_id") == "c1"]
    assert len(outs) == 1  # not doubled


# ---- truncation tally --------------------------------------------------


def test_tally_counts_truncated_calls():
    bad = {"type": "function_call", "call_id": "c1", "name": "write_file",
           "arguments": '{"path": "/x", "body": "unterminated'}
    tally = r.TruncationTally()
    r.make_input_filter(100000, truncation_tally=tally)(_call([bad]))
    assert tally.last == 1
    assert tally.total == 1


def test_tally_zero_on_clean_input():
    good = {"type": "function_call", "call_id": "c1", "name": "read_file",
           "arguments": '{"path": "/x"}'}
    tally = r.TruncationTally()
    r.make_input_filter(100000, truncation_tally=tally)(_call([good]))
    assert tally.last == 0
    assert tally.total == 0


def test_tally_accumulates_across_passes():
    bad = {"type": "function_call", "call_id": "c1", "name": "write_file",
           "arguments": '{"path": "/x", "body": "unterminated'}
    tally = r.TruncationTally()
    filt = r.make_input_filter(100000, truncation_tally=tally)
    filt(_call([bad]))
    filt(_call([bad]))
    # last reflects only the most recent pass; total is the running count.
    assert tally.last == 1
    assert tally.total == 2


def test_tally_counts_multiple_in_one_pass():
    b1 = {"type": "function_call", "call_id": "c1", "name": "write_file",
          "arguments": '{"path": "/x", "body": "unterminated'}
    b2 = {"type": "function_call", "call_id": "c2", "name": "write_file",
          "arguments": '{"path": "/y", "body": "also unterminated'}
    tally = r.TruncationTally()
    r.make_input_filter(100000, truncation_tally=tally)(_call([b1, b2]))
    assert tally.last == 2
    assert tally.total == 2


# ---- truncate over-long history ---------------------------------------


def test_truncates_overlong_history():
    system = {"role": "system", "content": "you are helpful"}
    users = [{"role": "user", "content": ("x" * 800) + str(i)} for i in range(30)]
    items = [system] + users
    # A budget far smaller than the total forces truncation.
    out = r.make_input_filter(2000)(_call(items))
    assert r._estimate(out.input) <= 2000 or len(out.input) <= r.MIN_KEEP_ITEMS + 1
    # The system prompt is always preserved and stays first.
    assert out.input[0]["role"] == "system"
    # The most recent item survives.
    assert out.input[-1]["content"].endswith("29")


def test_budget_callback_runs_before_truncation():
    items = [{"role": "user", "content": "x" * 2000} for _ in range(30)]
    observed = []

    out = r.make_input_filter(
        100,
        on_budget_exceeded=lambda current: observed.extend(current),
    )(_call(items))

    assert observed == items
    assert len(out.input) < len(items)


def test_budget_callback_can_interrupt_for_compaction():
    class CompactNow(Exception):
        pass

    def request_compaction(_items):
        raise CompactNow

    items = [{"role": "user", "content": "x" * 2000} for _ in range(5)]
    with pytest.raises(CompactNow):
        r.make_input_filter(100, on_budget_exceeded=request_compaction)(_call(items))


def test_truncate_drops_leading_orphan_output():
    system = {"role": "system", "content": "sys"}
    fc = {"type": "function_call", "call_id": "c1", "name": "write_file",
          "arguments": "{}"}
    fco = {"type": "function_call_output", "call_id": "c1", "output": "done"}
    users = [{"role": "user", "content": f"u{i}"} for i in range(3, 3 + r.MIN_KEEP_ITEMS)]
    # rest = [fc, fco] + users. A budget that keeps the last MIN_KEEP+1 items
    # drops fc and leaves fco as the leading item — which must then be dropped.
    rest = [fc, fco] + users
    items = [system] + rest
    keep = [fco] + users[: r.MIN_KEEP_ITEMS]
    budget = r._estimate([system]) + r._estimate(keep)
    out = r._truncate(items, budget)
    assert out[0]["role"] == "system"
    assert not (isinstance(out[1], dict) and out[1].get("type") == "function_call_output")
    assert out[1]["content"] == "u3"


# ---- filter never raises ----------------------------------------------


def test_filter_never_raises_on_garbage():
    garbage = [None, 123, "a string", {"no_type": True}, {"type": "function_call"}]
    out = r.make_input_filter(1000)(_call(garbage))
    assert isinstance(out, ModelInputData)
    assert isinstance(out.input, list)


def test_filter_handles_empty_input():
    out = r.make_input_filter(1000)(_call([]))
    assert isinstance(out, ModelInputData)
    assert out.input == []


# ---- tool error formatter ---------------------------------------------


class _FakeTool:
    def __init__(self, name):
        self.name = name


class _FakeAgent:
    def __init__(self, names):
        self.tools = [_FakeTool(n) for n in names]


class _FakeCtx:
    def __init__(self, names):
        self.agent = _FakeAgent(names)


def test_format_tool_error_lists_names_and_suggests():
    args = ToolErrorFormatterArgs(
        kind="tool_not_found",
        tool_name="write",
        agent_tool_names=("read_file", "write_file", "list_dir"),
    )
    msg = r.format_tool_error(args)
    assert "write_file" in msg
    assert "does not exist" in msg
    # closest match is suggested
    assert "write_file" in msg


def test_format_tool_error_ignores_other_kinds():
    args = ToolErrorFormatterArgs(
        kind="approval_rejected",
        tool_name="run_command",
        agent_tool_names=("run_command",),
    )
    assert r.format_tool_error(args) is None


def test_format_tool_error_no_tools():
    args = ToolErrorFormatterArgs(
        kind="tool_not_found",
        tool_name="frobnicate",
        agent_tool_names=(),
    )
    msg = r.format_tool_error(args)
    assert "(none)" in msg


# ---- closest-match helper ---------------------------------------------


def test_closest_substring_match():
    assert r._closest("write", ["read_file", "write_file"]) == "write_file"


def test_closest_prefix_match():
    assert r._closest("read", ["read_file", "write_file"]) == "read_file"


def test_closest_no_match():
    assert r._closest("zzz", ["abc", "def"]) is None


# ---- token calibrator -------------------------------------------------


def test_calibrator_starts_at_default_ratio():
    c = r.TokenCalibrator()
    assert c.chars_per_token == r.CHARS_PER_TOKEN


def test_calibrator_update_moves_ratio():
    c = r.TokenCalibrator()
    before = c.chars_per_token
    # 10000 chars for 2000 real tokens => measured 5.0 chars/token.
    c.update(10000, 2000)
    # Smoothed toward 5.0, so it must rise above the 2.4 default.
    assert c.chars_per_token > before
    assert c.chars_per_token < 5.0  # smoothed, not jumped


def test_calibrator_clamps_extreme_ratio():
    c = r.TokenCalibrator()
    # Absurdly high measured ratio (100 chars per token) clamps to the 6.0 cap.
    c.update(100000, 1000)
    assert c.chars_per_token <= 6.0
    # Absurdly low measured ratio clamps to the 1.5 floor.
    c2 = r.TokenCalibrator()
    c2.update(100, 1000)
    assert c2.chars_per_token >= 1.5


def test_calibrator_ignores_zero_inputs():
    c = r.TokenCalibrator()
    before = c.chars_per_token
    c.update(0, 100)
    c.update(100, 0)
    assert c.chars_per_token == before


def test_record_request_stores_char_count():
    c = r.TokenCalibrator()
    c.record_request(4321)
    assert c.last_char_count == 4321


def test_estimate_uses_calibrator_ratio():
    # A single item of known length; with a 4.0 ratio the cost is len//4.
    item = {"role": "user", "content": "x" * 400}
    cal = r.TokenCalibrator(initial=4.0)
    est = r.estimate_input_tokens([item], cal)
    # The weighted JSON is longer than the bare content, but the ratio is 4.0.
    weighted_len = len(r._weight(item))
    assert est == max(1, weighted_len // 4)


def test_filter_uses_calibrated_ratio_for_truncation():
    # With a tighter (lower) ratio the same content costs more tokens and so
    # truncates sooner than with the default ratio. Use more items than
    # MIN_KEEP_ITEMS so truncation can actually drop some.
    items = [{"role": "user", "content": "x" * 500} for _ in range(20)]
    tight = r.TokenCalibrator(initial=1.5)  # over-counts -> truncates more
    out = r.make_input_filter(1500, calibrator=tight)(_call(items))
    assert len(out.input) < len(items)
