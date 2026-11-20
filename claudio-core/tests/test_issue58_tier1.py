"""Issue #58 Increment 3 -- Tier-1 deterministic (zero-LLM) history reduction.

Pins the pure algorithm: failed payloads pruned, superseded reads deduped to
the latest copy, stale bulky outputs collapsed -- all only outside the
protected recent window, all idempotent, and the stats accounting for what was
saved. No LLM is involved anywhere; this is the pass that must make zero
``purpose="compaction"`` calls while cutting history tokens.

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_issue58_tier1.py -q
"""
from __future__ import annotations

from claudio_core.tier1 import plan_tier1


def _tr(text, *, tool="read_file", error=False, key=None):
    return {
        "text": text,
        "is_tool_result": True,
        "is_error": error,
        "tool": tool,
        "dedupe_key": key,
    }


def _other(text="hello"):
    return {
        "text": text,
        "is_tool_result": False,
        "is_error": False,
        "tool": "",
        "dedupe_key": None,
    }


def test_recent_turns_are_never_touched():
    # A bulky read inside the protected recent window survives untouched.
    turns = [_other() for _ in range(6)] + [_tr("x" * 5000, key="a.py")]
    repl, stats = plan_tier1(turns, keep_recent_turns=6)
    assert repl[6] is None  # last turn is recent -> protected
    assert not stats.changed


def test_old_turn_outside_window_collapsed():
    # 7 turns, keep 6 -> index 0 is old; its bulky read collapses.
    turns = [_tr("x" * 5000, key="a.py")] + [_other() for _ in range(6)]
    repl, stats = plan_tier1(turns, keep_recent_turns=6)
    assert repl[0] is not None
    assert stats.collapsed_outputs == 1


def test_stale_bulky_output_collapsed():
    turns = [_tr("y" * 3000, key="big.txt")] + [_other() for _ in range(6)]
    repl, stats = plan_tier1(turns, keep_recent_turns=6, bulky_chars=1200)
    assert repl[0] is not None
    assert "collapsed" in repl[0]
    assert repl[0].startswith("yyy")  # keeps head
    assert stats.collapsed_outputs == 1
    assert stats.chars_saved > 1000


def test_failed_payload_pruned():
    turns = [_tr("boom " * 100, error=True, tool="run_command")] + [
        _other() for _ in range(6)
    ]
    repl, stats = plan_tier1(turns, keep_recent_turns=6)
    assert repl[0] is not None
    assert "failed run_command" in repl[0]
    assert stats.pruned_failures == 1


def test_superseded_reads_deduped_to_latest():
    # Same file read three times, all old; only the last keeps full text.
    turns = [
        _tr("old content " * 50, key="a.py"),
        _other(),
        _tr("mid content " * 50, key="a.py"),
        _other(),
        _tr("newest content " * 50, key="a.py"),
        _other(),
        _other(),
    ]
    repl, stats = plan_tier1(turns, keep_recent_turns=2)
    assert repl[0] is not None and "superseded" in repl[0]
    assert repl[2] is not None and "superseded" in repl[2]
    assert repl[4] is None  # the latest copy survives
    assert stats.deduped_reads == 2


def test_plan_is_idempotent():
    turns = [_tr("z" * 3000, key="f")] + [_other() for _ in range(6)]
    repl1, _ = plan_tier1(turns)
    # Apply the plan, then re-plan over the reduced turns: nothing more changes.
    applied = [
        (dict(t, text=r) if r is not None else t) for t, r in zip(turns, repl1)
    ]
    repl2, stats2 = plan_tier1(applied)
    assert all(r is None for r in repl2)
    assert not stats2.changed


def test_stats_changed_flag():
    turns = [_other() for _ in range(8)]
    _, stats = plan_tier1(turns)
    assert not stats.changed
    d = stats.as_dict()
    assert set(d) == {"pruned_failures", "deduped_reads", "collapsed_outputs", "chars_saved"}


def test_non_tool_turns_untouched():
    turns = [_other("a" * 5000)] + [_other() for _ in range(6)]
    repl, stats = plan_tier1(turns)
    assert repl[0] is None
    assert not stats.changed
