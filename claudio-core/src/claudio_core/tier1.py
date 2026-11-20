"""Tier-1 deterministic (zero-LLM) history reduction.

Issue #58 (Performance Improvement Plan), Increment 3. Input tokens dominate
multi-turn agent cost, and most of those tokens are *stale tool output*: file
contents that were re-read later, bulky command dumps that were already acted
on, and failed calls whose payload only records the mistake. None of that
needs a language model to prune -- it is structural, and deterministic code
does it for free.

This module is the algorithm; it is deliberately framework-free and pure: it
takes a flat list of turn descriptors and returns a replacement text (or
``None`` to keep the turn untouched) for each, plus stats. The engine adapts
its own conversation representation to this shape and applies the plan by
shrinking payload text in place -- it never drops or reorders turns, so
tool-call/response pairing is preserved by construction.

The three reductions, in priority order:

1. **Prune failed payloads** -- an old error result keeps only a one-line
   marker; the model already saw it and moved on.
2. **Dedupe superseded reads** -- when the same resource (a file path) was
   read more than once, every read except the most recent collapses to a
   marker. The latest read is the only one that reflects current state.
3. **Collapse stale bulky outputs** -- an old tool result above the bulk
   threshold keeps a short head excerpt; the tail is replaced by a marker.

"Old" means outside the protected recent window (default: the last
``keep_recent_turns`` turns), so the model always sees full, current detail
for what it is actively working on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Turns newer than this many are never touched (the active working set).
DEFAULT_KEEP_RECENT_TURNS = 6
#: A tool result longer than this (chars) is a collapse candidate.
DEFAULT_BULKY_CHARS = 1200
#: How much head text a collapsed bulky output keeps.
DEFAULT_STUB_CHARS = 200

_FAILED_MARKER = "[failed {tool} call — error output pruned by deterministic compaction]"
_SUPERSEDED_MARKER = "[superseded {tool} result — re-read later; this copy is stale]"


def _collapse_stub(text: str, stub_chars: int) -> str:
    head = text[:stub_chars]
    dropped = len(text) - len(head)
    return f"{head}\n[…collapsed {dropped} chars of stale tool output…]"


@dataclass
class Tier1Stats:
    """What a Tier-1 pass changed -- feeds the lifecycle counters."""

    pruned_failures: int = 0
    deduped_reads: int = 0
    collapsed_outputs: int = 0
    chars_saved: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.pruned_failures or self.deduped_reads or self.collapsed_outputs
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "pruned_failures": self.pruned_failures,
            "deduped_reads": self.deduped_reads,
            "collapsed_outputs": self.collapsed_outputs,
            "chars_saved": self.chars_saved,
        }


def plan_tier1(
    turns: list[dict[str, Any]],
    *,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
    bulky_chars: int = DEFAULT_BULKY_CHARS,
    stub_chars: int = DEFAULT_STUB_CHARS,
) -> tuple[list[str | None], Tier1Stats]:
    """Compute a Tier-1 reduction plan for a list of turn descriptors.

    Each descriptor is a dict::

        {"text": str, "is_tool_result": bool, "is_error": bool,
         "tool": str, "dedupe_key": str | None}

    ``dedupe_key`` identifies the resource a read targeted (e.g. the file path
    for ``read_file``); only read-like turns should set it. Returns a list the
    same length as ``turns`` where each entry is either ``None`` (keep as-is)
    or the replacement text, plus a :class:`Tier1Stats` summary.

    The function is pure and order-stable: the same input always yields the
    same plan, which is what lets the engine apply it every turn without
    flapping the prompt prefix.
    """
    n = len(turns)
    replacements: list[str | None] = [None] * n
    stats = Tier1Stats()
    recent_from = max(0, n - keep_recent_turns)

    # 1. Prune failed payloads outside the recent window.
    for i in range(recent_from):
        t = turns[i]
        if t.get("is_tool_result") and t.get("is_error"):
            text = t.get("text") or ""
            if len(text) > 0:
                marker = _FAILED_MARKER.format(tool=t.get("tool") or "tool")
                replacements[i] = marker
                stats.pruned_failures += 1
                stats.chars_saved += max(0, len(text) - len(marker))

    # 2. Dedupe superseded reads: for each dedupe_key, keep the LAST
    #    occurrence (even if old -- it is the current view) and collapse the
    #    earlier ones. Occurrences inside the recent window are protected.
    last_index_for_key: dict[str, int] = {}
    for i, t in enumerate(turns):
        key = t.get("dedupe_key")
        if key and t.get("is_tool_result") and not t.get("is_error"):
            last_index_for_key[key] = i
    for key, last_i in last_index_for_key.items():
        for i, t in enumerate(turns):
            if i == last_i or replacements[i] is not None:
                continue
            if t.get("dedupe_key") != key:
                continue
            if i >= recent_from:
                continue  # protected recent turn
            text = t.get("text") or ""
            marker = _SUPERSEDED_MARKER.format(tool=t.get("tool") or "read")
            replacements[i] = marker
            stats.deduped_reads += 1
            stats.chars_saved += max(0, len(text) - len(marker))

    # 3. Collapse stale bulky outputs (not already handled above).
    for i in range(recent_from):
        if replacements[i] is not None:
            continue
        t = turns[i]
        if not t.get("is_tool_result") or t.get("is_error"):
            continue
        text = t.get("text") or ""
        if len(text) > bulky_chars:
            stub = _collapse_stub(text, stub_chars)
            replacements[i] = stub
            stats.collapsed_outputs += 1
            stats.chars_saved += len(text) - len(stub)

    return replacements, stats


__all__ = [
    "Tier1Stats",
    "plan_tier1",
    "DEFAULT_KEEP_RECENT_TURNS",
    "DEFAULT_BULKY_CHARS",
    "DEFAULT_STUB_CHARS",
]
