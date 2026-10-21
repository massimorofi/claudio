"""Preventive request sanitization — the "prevent, don't react" layer.

Runs as a ``call_model_input_filter`` immediately before EVERY model call, so
the request that reaches the OpenAI-compatible engine is always well-formed.
This eliminates the class of failures where one bad history item (a truncated
tool-call body, an orphaned call, an over-long conversation) makes the engine
reject every subsequent request with a 400 — the root cause of the "searches
forever then returns nothing" symptom.

Complements Claudio's summary compaction; does not replace it. The compactor is
the meaning-preserving compressor; this is the cheap, deterministic structural
safety net that guarantees the bytes on the wire are valid.

Design contract (see docs/implementation-plan-prevent-not-react.md):
- The filter returns a ``ModelInputData``. On an internal sanitization error it
  returns the input unchanged — a broken filter is worse than no filter.
- When configured, an over-budget callback may raise a control-flow signal so
  the run manager can compact before the filter falls back to truncation.
- Truncation preserves call/output pairing: never leave a ``function_call``
  without its ``function_call_output``, nor a leading orphaned output.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

# --------------------------------------------------------------------------
# Neutral request/response shapes.
#
# Upstream these three types came from ``agents.run_config``. They are plain
# data carriers, so core defines its own: the input filter is framework logic,
# not framework policy. An engine adapts its native request object to
# ``CallModelData`` on the way in and reads ``ModelInputData`` on the way out.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInputData:
    """The prompt payload about to be sent to the model."""

    input: list[Any] = field(default_factory=list)
    instructions: str = ""


@dataclass(frozen=True)
class CallModelData:
    """Everything available at the moment a model call is made.

    ``model_data`` is the request; ``context`` carries the run-level
    :class:`~claudio_core.context.ToolContext` (or ``None`` when the engine
    has no richer context to offer).
    """

    model_data: ModelInputData
    context: Any = None


@dataclass(frozen=True)
class ToolErrorFormatterArgs:
    """A tool-execution failure the engine wants rendered for the model."""

    kind: str
    tool_name: str
    agent_tool_names: tuple[str, ...] = ()

logger = logging.getLogger(__name__)

# Conservative token estimate. The common "~4 chars/token" heuristic is a
# *prose* figure and badly undercounts structured content: measured against this
# model, plain English is ~4.5 chars/token but JSON/code is ~2.4. A filter that
# undercounts lets an over-long request through to the engine, which is exactly
# the overflow we are trying to prevent. We deliberately cost low (2.4) so the
# estimate OVER-counts real tokens for every content type we have measured —
# over-truncating is a soft, recoverable loss; overflow is a hard failure.
CHARS_PER_TOKEN = 2.4
# A base64 image is not text and must not be costed as if it were; charge a flat
# per-image allowance so one screenshot doesn't truncate the whole conversation.
IMAGE_TOKEN_ESTIMATE = 1_500
# Never truncate below this many recent items, regardless of the token budget.
MIN_KEEP_ITEMS = 12

# A tool call whose arguments are not valid JSON is replaced with this. An empty
# object satisfies any "type": "object" schema, so the engine accepts the
# replayed history; the paired tool output tells the model what happened.
_TRUNCATED_ARGS = "{}"
_TRUNCATED_NOTE = (
    "Your previous call to '{name}' was cut off before its arguments were "
    "complete, so it never ran. Do not retry it as-is. Send smaller pieces "
    "(<= {max_write_chars} chars per call); for files, write the first part "
    "then continue with append=true."
)

# Default per-call write cap referenced in the truncation note. Overridden by the
# caller when a tighter budget is configured.
_DEFAULT_MAX_WRITE_CHARS = 8000


class TruncationTally:
    """Mutable counter the input filter updates each pass.

    The filter repairs tool-call arguments that arrived as invalid JSON — the
    signature of a model response that was cut off before the call finished.
    ``last`` is how many such calls it found in the most recent pass; ``total``
    is the running count across the run. The run manager reads these to decide
    when the model is stuck producing truncated output and must stop with a
    clear message instead of looping. See §3.6.14.
    """

    __slots__ = ("last", "total")

    def __init__(self) -> None:
        self.last = 0
        self.total = 0


def _is_image_part(part: Any) -> bool:
    return (
        isinstance(part, dict)
        and part.get("type") in ("input_image", "image_url", "image")
    )


def _weight(item: Any) -> str:
    """Serialise one item for costing, with image payloads stripped.

    Image parts carry megabytes of base64 that is not text, so they are removed
    here and costed separately as a flat per-image allowance.
    """
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, list):
            item = {**item, "content": [p for p in content if not _is_image_part(p)]}
    if isinstance(item, (dict, list)):
        try:
            return json.dumps(item, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(item)
    return str(item)


class TokenCalibrator:
    """Adaptive chars-per-token ratio for the token estimate.

    ``CHARS_PER_TOKEN`` is a generic guess. Once the model reports real
    ``prompt_tokens`` for a request we can measure the true ratio for the
    content actually being sent and use it for every later estimate, so the
    estimate tracks this specific model and content mix instead of a fixed
    guess. The ratio is clamped to a sane band and exponentially smoothed so a
    single odd turn (a tool dump, a near-empty turn) cannot swing it.
    """

    def __init__(self, initial: float = CHARS_PER_TOKEN) -> None:
        self.chars_per_token = initial
        self.last_char_count = 0

    def record_request(self, char_count: int) -> None:
        """Remember the conversation char count of the request just sent."""
        self.last_char_count = char_count

    def update(self, char_count: int, actual_tokens: int) -> None:
        """Fold one (chars, real_tokens) observation into the ratio."""
        if char_count <= 0 or actual_tokens <= 0:
            return
        measured = char_count / actual_tokens
        measured = max(1.5, min(6.0, measured))
        self.chars_per_token = 0.7 * self.chars_per_token + 0.3 * measured


def _item_tokens(item: Any, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Cost one item: text at ``chars_per_token``, images at a flat allowance."""
    tokens = max(1, int(len(_weight(item)) // chars_per_token))
    if isinstance(item, dict) and isinstance(item.get("content"), list):
        tokens += sum(1 for p in item["content"] if _is_image_part(p)) * IMAGE_TOKEN_ESTIMATE
    return tokens


def _estimate(items: list[Any], chars_per_token: float = CHARS_PER_TOKEN) -> int:
    return sum(_item_tokens(it, chars_per_token) for it in items)


def _char_count(items: list[Any]) -> int:
    """Total weighted character count of the items (for calibration)."""
    return sum(len(_weight(it)) for it in items)


def estimate_input_tokens(
    items: list[Any], calibrator: "TokenCalibrator | None" = None
) -> int:
    """Estimate input tokens with the same structured-content rules as the filter."""
    cpt = calibrator.chars_per_token if calibrator is not None else CHARS_PER_TOKEN
    return _estimate(items, cpt)


def _args_valid(raw: Any) -> bool:
    """True when a tool-call ``arguments`` value parses as a JSON object."""
    if isinstance(raw, dict):
        return True
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        return isinstance(json.loads(raw), dict)
    except (ValueError, TypeError):
        return False


def _cap_item_text(item: Any, max_chars: int) -> Any:
    """Return a copy of one item with its text content clipped to ``max_chars``.

    Only the human-readable text fields are clipped — ``content`` (string or
    text parts) and a ``function_call_output``'s ``output``. A tool call's
    ``arguments`` is left untouched: it is JSON, and cutting it mid-string
    would make the replayed history invalid. This bounds the cost of a single
    oversized item (a 20k-char file read, a giant command dump) so the
    keep-floor below cannot retain a request that blows the window.
    """
    if not isinstance(item, dict):
        return item
    out = item
    content = out.get("content")
    if isinstance(content, str) and len(content) > max_chars:
        out = {**out, "content": content[:max_chars] + "\n[…truncated]"}
    elif isinstance(content, list):
        new_parts: list[Any] = []
        changed = False
        for part in content:
            if (
                isinstance(part, dict)
                and isinstance(part.get("text"), str)
                and len(part["text"]) > max_chars
            ):
                new_parts.append({**part, "text": part["text"][:max_chars] + "\n[…truncated]"})
                changed = True
            else:
                new_parts.append(part)
        if changed:
            out = {**out, "content": new_parts}
    output = out.get("output")
    if isinstance(output, str) and len(output) > max_chars:
        out = {**out, "output": output[:max_chars] + "\n[…truncated]"}
    return out


def _truncate(
    items: list[Any], max_tokens: int, chars_per_token: float = CHARS_PER_TOKEN
) -> list[Any]:
    """Keep the system prompt + the most recent items that fit ``max_tokens``.

    A cheap deterministic slice — no model call. This is the last-resort guard
    against context overflow; the summary compactor is the meaning-preserving
    layer that normally keeps us under budget long before this fires.

    The keep-floor (``MIN_KEEP_ITEMS``) is a *soft* target: it is capped by the
    budget so that retaining it can never itself exceed the window. Each retained
    item's text is clipped to a per-item ceiling sized so that ``MIN_KEEP_ITEMS``
    of them fit the budget. Without this cap, a few very large recent items (big
    file reads, large command outputs) would be force-retained by the floor and
    overflow the model window — the exact failure this layer exists to prevent.
    """
    if _estimate(items, chars_per_token) <= max_tokens:
        return items

    system = [i for i in items if isinstance(i, dict) and i.get("role") == "system"]
    rest = [i for i in items if not (isinstance(i, dict) and i.get("role") == "system")]

    budget = max(0, max_tokens - _estimate(system, chars_per_token))
    # Per-item character ceiling: sized so MIN_KEEP_ITEMS capped items fill the
    # budget. Floored at 1200 chars so a tight budget still keeps usable text.
    per_item_cap = max(1200, int(budget * chars_per_token / max(1, MIN_KEEP_ITEMS)))
    kept: list[Any] = []
    spent = 0
    for it in reversed(rest):
        it = _cap_item_text(it, per_item_cap)
        cost = _item_tokens(it, chars_per_token)
        if spent + cost > budget and len(kept) >= MIN_KEEP_ITEMS:
            break
        kept.insert(0, it)
        spent += cost

    # Never begin the kept window with an orphaned tool output (its matching
    # call was cut off in the dropped head) — that is itself invalid input.
    while kept and isinstance(kept[0], dict) and kept[0].get("type") == "function_call_output":
        kept.pop(0)

    logger.warning(
        "input filter truncated history %d -> %d items (est %d tokens > %d)",
        len(items),
        len(system) + len(kept),
        _estimate(items, chars_per_token),
        max_tokens,
    )
    return system + kept


def _repair_calls(items: list[Any], max_write_chars: int) -> tuple[list[Any], int]:
    """Replace invalid function_call arguments and close dangling calls.

    Two structural repairs, both required for chat-completions templates:
    1. A ``function_call`` whose ``arguments`` is not valid JSON becomes ``{}``
       with a paired output explaining what happened.
    2. Any ``function_call`` left without a matching ``function_call_output``
       gets a synthetic output so the template never sees an orphan.
    """
    answered = {
        i.get("call_id")
        for i in items
        if isinstance(i, dict) and i.get("type") == "function_call_output"
    }

    out: list[Any] = []
    fixed = 0
    for it in items:
        if not isinstance(it, dict):
            out.append(it)
            continue
        if it.get("type") == "function_call" and not _args_valid(it.get("arguments")):
            cid = it.get("call_id") or ""
            out.append({**it, "arguments": _TRUNCATED_ARGS})
            # Only add the explanation when nothing else answers the call; a
            # second output for the same call_id is itself invalid.
            if cid and cid not in answered:
                out.append(
                    {
                        "type": "function_call_output",
                        "call_id": cid,
                        "output": _TRUNCATED_NOTE.format(
                            name=it.get("name") or "tool",
                            max_write_chars=max_write_chars,
                        ),
                    }
                )
                answered.add(cid)
            fixed += 1
            continue
        out.append(it)

    # Close any call still without an output.
    answered2 = {
        i.get("call_id")
        for i in out
        if isinstance(i, dict) and i.get("type") == "function_call_output"
    }
    final: list[Any] = []
    for it in out:
        final.append(it)
        if (
            isinstance(it, dict)
            and it.get("type") == "function_call"
            and it.get("call_id")
            and it["call_id"] not in answered2
        ):
            final.append(
                {
                    "type": "function_call_output",
                    "call_id": it["call_id"],
                    "output": "No result recorded. Treat as not done; decide next step.",
                }
            )
            answered2.add(it["call_id"])
    return final, fixed


def make_input_filter(
    max_context_tokens: int,
    max_write_chars: int = _DEFAULT_MAX_WRITE_CHARS,
    on_budget_exceeded: Callable[[list[Any]], None] | None = None,
    calibrator: "TokenCalibrator | None" = None,
    truncation_tally: "TruncationTally | None" = None,
):
    """Build a ``call_model_input_filter`` closed over the token ceiling.

    ``max_context_tokens`` is the hard prompt budget (window minus response
    headroom and fixed request overhead). Before truncating, the optional
    callback can request meaning-preserving compaction from the run manager.
    ``calibrator`` (when supplied) makes the size estimate track the model's
    real tokenization rather than the static heuristic.
    """

    def _cpt() -> float:
        return calibrator.chars_per_token if calibrator is not None else CHARS_PER_TOKEN

    def sanitize(data: CallModelData) -> ModelInputData:
        try:
            items = list(data.model_data.input or [])
            if calibrator is not None:
                # Remember the exact conversation size we are about to send so
                # the manager can pair it with the model's reported token count
                # and calibrate the ratio against reality.
                calibrator.record_request(_char_count(items))
            over_budget = bool(items) and _estimate(items, _cpt()) > max_context_tokens
        except Exception as exc:  # noqa: BLE001 - preserve the no-op safety fallback
            logger.error(
                "input filter sizing failed, passing input through unchanged: %s",
                exc,
            )
            return ModelInputData(
                input=list(data.model_data.input or []),
                instructions=data.model_data.instructions,
            )

        if over_budget and on_budget_exceeded is not None:
            # Deliberately outside the sanitization guard: the run manager uses
            # this callback to raise a control-flow signal that starts compaction.
            on_budget_exceeded(items)

        try:
            items = _truncate(items, max_context_tokens, _cpt())
            items, fixed = _repair_calls(items, max_write_chars)
            if truncation_tally is not None:
                truncation_tally.last = fixed
                truncation_tally.total += fixed
            if fixed:
                logger.warning(
                    "input filter repaired %d truncated tool call(s)", fixed
                )
            return ModelInputData(
                input=items, instructions=data.model_data.instructions
            )
        except Exception as exc:  # noqa: BLE001 - a filter must never break the run
            logger.error(
                "input filter failed, passing input through unchanged: %s", exc
            )
            return ModelInputData(
                input=list(data.model_data.input or []),
                instructions=data.model_data.instructions,
            )

    return sanitize


def _closest(name: str, candidates: list[str]) -> str | None:
    """Cheapest useful suggestion: substring first, then shared-prefix."""
    low = name.lower()
    for c in candidates:
        cl = c.lower()
        if low in cl or cl in low:
            return c
    best: tuple[int, str] | None = None
    for c in candidates:
        n = 0
        for x, y in zip(low, c.lower()):
            if x != y:
                break
            n += 1
        if n >= 3 and (best is None or n > best[0]):
            best = (n, c)
    return best[1] if best else None


def format_tool_error(args: ToolErrorFormatterArgs) -> str | None:
    """Turn a tool-not-found error into guidance the model can act on.

    The SDK's default message ("tool not found") is true and useless. This names
    the tools that DO exist and suggests the closest match, which is usually all
    the model needs to recover on the next turn instead of the run aborting.

    Returns ``None`` for non-tool-not-found kinds so the SDK default stands.
    """
    if getattr(args, "kind", None) != "tool_not_found":
        return None

    names: list[str] = [n for n in args.agent_tool_names if n]

    hint = _closest(args.tool_name, names)
    msg = (
        f"Tool '{args.tool_name}' does not exist. "
        f"Available tools: {', '.join(names) if names else '(none)'}."
    )
    msg += (
        f" Did you mean '{hint}'? Call it by that exact name."
        if hint
        else " Do not call tools outside that list."
    )
    return msg


__all__ = [
    "make_input_filter",
    "format_tool_error",
    "TruncationTally",
    "CallModelData",
    "ModelInputData",
    "ToolErrorFormatterArgs",
]
