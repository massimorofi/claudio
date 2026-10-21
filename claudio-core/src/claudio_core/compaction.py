"""Local context compaction with circuit breakers (Claudio §5.3).

When a conversation's input tokens approach the context window, older history
is replaced by a single structured summary. A circuit breaker disables
auto-compaction for a session after repeated failures so a broken summariser
cannot loop forever.

The summariser is **injected** as an async callable rather than constructed
here. Upstream this module built an ``Agent`` and ran it with a ``Runner``,
which meant core could not compact without the agent framework installed. Now
core owns the policy (when to compact, how to render, when to give up) and the
engine owns the model call.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Consecutive compaction failures that trip the breaker.
CIRCUIT_BREAKER_THRESHOLD = 3

#: How long a single summariser call may take before we give up on it.
#:
#: This is not a formality. The default must be generous enough for a slow local
#: reasoning model to actually finish: a 120s cap was observed timing out on a
#: long build, tripping the breaker three times, and permanently disabling
#: compaction for the session — which then corrupted tool-call arguments during
#: truncation and killed the run. Compaction is the thing that prevents overflow,
#: so its timeout has to be sized for the slowest hardware we support.
DEFAULT_COMPACTION_TIMEOUT_SECONDS = 300.0

#: Maximum characters of conversation handed to the summariser in one request.
#:
#: An unbounded transcript can exceed the summarising model's own context
#: window and time out, silently defeating compaction on exactly the long
#: conversations that need it most. Over the cap we keep the opening (the
#: original request and early context) and the closing (the current state),
#: which together carry what the summary most needs, and drop the middle.
MAX_SUMMARY_INPUT_CHARS = 12000

SUMMARY_PROMPT = """You are compacting a conversation to save context. Write a \
structured summary that preserves everything needed to continue the work. Include:

1. Primary request and intent
2. Key decisions made (exact wording where it matters)
3. Files and code sections touched
4. Errors encountered and how they were resolved
5. Current state and what remains to be done
6. Hard-to-reconstruct specifics (names, numbers, paths, links)

ALWAYS end the summary with an explicit progress checklist in exactly this form:

PROGRESS:
DONE: <comma-separated list of concrete steps already completed, with file names \
where relevant>
TODO: <comma-separated list of concrete steps still remaining, in order>

This checklist is the ONLY record of progress that survives compaction. The next \
turn relies on it to know what not to redo. Make each item specific and \
actionable (a file to write, a command to run), not vague. If the work is a \
build/create task, TODO must name the specific files still to be created.

Be concise but complete. Do not call any tools. Write only the summary.\n\n"""


class CompactionFailedError(Exception):
    """Raised when compaction has failed and the circuit breaker is now tripped.

    This is a *terminal* condition for the current run. Once compaction is
    dead the only remaining fallback is truncation, and truncation corrupts
    tool-call arguments (see ``claudio_core.recovery._repair_calls``) — which
    is exactly what made a long build run fail with "tool calls failing" while
    the real cause was the dead compactor. Rather than limping along until that
    corruption bites, the run stops here with a clear operator-facing message.
    """


#: A summariser takes the rendered transcript and returns the summary text.
Summariser = Callable[[str], Awaitable[str]]


class Compactor:
    """Decides when to compact, renders the transcript, tracks failures.

    ``summarise`` is the injected model call. It may raise; the caller decides
    whether that is fatal, because only the caller knows how many attempts the
    run has left.
    """

    def __init__(
        self,
        context_window: int,
        reserve: int,
        max_compact_tokens: int = 2000,
        timeout_seconds: float = DEFAULT_COMPACTION_TIMEOUT_SECONDS,
    ) -> None:
        self.context_window = context_window
        self.reserve = reserve
        self.max_compact_tokens = max_compact_tokens
        self.timeout_seconds = timeout_seconds
        self._failures: dict[str, int] = {}
        self._disabled: set[str] = set()

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def should_compact(self, input_tokens: int) -> bool:
        compact_at = self.context_window - self.reserve
        return input_tokens >= compact_at

    def is_disabled(self, session_id: str) -> bool:
        return session_id in self._disabled

    def disable(self, session_id: str) -> None:
        """Explicitly disable auto-compaction for a session.

        Used to restore persisted breaker state on startup (the
        ``claudio_sessions.compaction_disabled`` column).
        """
        self._disabled.add(session_id)

    def enable(self, session_id: str) -> None:
        """Re-enable auto-compaction for a session and clear its failure count."""
        self._failures[session_id] = 0

    def record_failure(self, session_id: str) -> None:
        self._failures[session_id] = self._failures.get(session_id, 0) + 1
        if self._failures[session_id] >= CIRCUIT_BREAKER_THRESHOLD:
            self._disabled.add(session_id)
            logger.warning(
                "Compaction circuit breaker tripped for session %s", session_id
            )

    def record_success(self, session_id: str) -> None:
        self._failures[session_id] = 0

    # ------------------------------------------------------------------
    # Compaction
    # ------------------------------------------------------------------

    def build_summary_request(self, items: list[dict[str, Any]]) -> str | None:
        """Render session items into the summariser prompt.

        Returns ``None`` when there is nothing worth compacting.
        """
        transcript = render_items(items)
        if not transcript.strip():
            return None
        if len(transcript) > MAX_SUMMARY_INPUT_CHARS:
            half = MAX_SUMMARY_INPUT_CHARS // 2
            transcript = (
                transcript[:half]
                + "\n\n[... middle of conversation omitted for length ...]\n\n"
                + transcript[-half:]
            )
        return SUMMARY_PROMPT + transcript

    async def summarize(
        self, items: list[dict[str, Any]], summarise: Summariser, session_id: str
    ) -> str:
        """Produce a summary, recording the outcome on the breaker.

        Raises :class:`CompactionFailedError` when the breaker is already
        tripped, or when this attempt fails and trips it. A single failure that
        does not trip the breaker re-raises the underlying error so the caller
        can retry with a different strategy.
        """
        if self.is_disabled(session_id):
            raise CompactionFailedError(
                f"Compaction is disabled for session {session_id}."
            )

        request = self.build_summary_request(items)
        if request is None:
            return ""

        try:
            summary = await asyncio.wait_for(summarise(request), self.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - any summariser failure feeds the breaker
            self.record_failure(session_id)
            logger.warning("Compaction failed for session %s: %s", session_id, exc)
            if self.is_disabled(session_id):
                raise CompactionFailedError(
                    f"Compaction failed {CIRCUIT_BREAKER_THRESHOLD} times for "
                    f"session {session_id}; auto-compaction is now off."
                ) from exc
            raise

        if not summary or not str(summary).strip():
            self.record_failure(session_id)
            logger.warning("Compaction produced an empty summary for session %s", session_id)
            raise ValueError("empty summary")

        self.record_success(session_id)
        logger.info("Compacted session %s (%d items → summary)", session_id, len(items))
        return str(summary)

    async def compact(
        self, items: list[dict[str, Any]], summarise: Summariser, session_id: str
    ) -> bool:
        """Backwards-compatible boolean wrapper around :meth:`summarize`.

        Returns ``True`` when a summary was produced, ``False`` when the
        session was too short or compaction is disabled.
        """
        if self.is_disabled(session_id):
            return False
        if len(items) < 4:
            return False
        try:
            await self.summarize(items, summarise, session_id)
            return True
        except (CompactionFailedError, ValueError, asyncio.TimeoutError):
            return False


def render_items(items: list[dict[str, Any]]) -> str:
    """Flatten a session's items into plain text for the summariser."""
    parts: list[str] = []
    for item in items:
        role = item.get("role", "?")
        content = item.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") in ("text", "output_text"):
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[tool call: {block.get('name')}]")
                    elif block.get("type") == "tool_result":
                        text_parts.append("[tool result]")
            content = " ".join(text_parts)
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


__all__ = [
    "Compactor",
    "CompactionFailedError",
    "Summariser",
    "render_items",
    "CIRCUIT_BREAKER_THRESHOLD",
    "MAX_SUMMARY_INPUT_CHARS",
    "DEFAULT_COMPACTION_TIMEOUT_SECONDS",
    "SUMMARY_PROMPT",
]
