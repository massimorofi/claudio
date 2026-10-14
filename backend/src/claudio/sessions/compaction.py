"""Local context compaction with circuit breakers (Claudio §5.3).

When the conversation's input tokens approach the context window, older history is
summarized into a single structured summary that replaces it. A circuit breaker
disables auto-compaction for a session after repeated failures so a broken
summarizer can't loop forever.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Consecutive compaction failures that trip the breaker.
CIRCUIT_BREAKER_THRESHOLD = 3

SUMMARY_PROMPT = """You are compacting a conversation to save context. Write a \
structured summary that preserves everything needed to continue the work. Include:

1. Primary request and intent
2. Key decisions made (exact wording where it matters)
3. Files and code sections touched
4. Errors encountered and how they were resolved
5. Current state and what remains to be done
6. Hard-to-reconstruct specifics (names, numbers, paths, links)

Be concise but complete. Do not call any tools. Write only the summary.\n\n"""


class Compactor:
    def __init__(
        self,
        context_window: int,
        reserve: int,
        max_compact_tokens: int = 2000,
    ) -> None:
        self.context_window = context_window
        self.reserve = reserve
        self.max_compact_tokens = max_compact_tokens
        self._failures: dict[str, int] = {}
        self._disabled: set[str] = set()

    def should_compact(self, input_tokens: int) -> bool:
        compact_at = self.context_window - self.reserve
        return input_tokens >= compact_at

    def is_disabled(self, session_id: str) -> bool:
        return session_id in self._disabled

    def record_failure(self, session_id: str) -> None:
        self._failures[session_id] = self._failures.get(session_id, 0) + 1
        if self._failures[session_id] >= CIRCUIT_BREAKER_THRESHOLD:
            self._disabled.add(session_id)
            logger.warning(
                "Compaction circuit breaker tripped for session %s", session_id
            )

    def record_success(self, session_id: str) -> None:
        self._failures[session_id] = 0

    async def compact(
        self,
        session,  # SQLiteSession
        model,  # OpenAIChatCompletionsModel
        session_id: str,
    ) -> bool:
        """Summarize the session's history in place. Returns True on success."""
        if self.is_disabled(session_id):
            return False
        items = await session.get_items()
        if len(items) < 4:
            return False  # nothing worth compacting

        # Render the conversation into text for the summarizer.
        transcript = _render_items(items)
        if not transcript.strip():
            return False

        try:
            from agents import Runner, Agent

            summarizer = Agent(
                name="compactor",
                instructions="You are a precise conversation summarizer.",
                model=model,
                output_type=str,
            )
            result = await Runner.run(
                summarizer,
                SUMMARY_PROMPT + transcript,
                max_turns=1,
            )
            summary = result.final_output
            if not summary or not str(summary).strip():
                raise ValueError("empty summary")
        except Exception as e:  # noqa: BLE001
            self.record_failure(session_id)
            logger.warning("Compaction failed for session %s: %s", session_id, e)
            return False

        # Replace history with the summary.
        await session.clear_session()
        await session.add_items(
            [
                {
                    "role": "user",
                    "content": (
                        "This session is being continued from a previous conversation "
                        "that ran out of context. The summary below covers the earlier "
                        "portion.\n\n" + str(summary)
                    ),
                }
            ]
        )
        self.record_success(session_id)
        logger.info("Compacted session %s (%d items → summary)", session_id, len(items))
        return True


def _render_items(items: list[dict[str, Any]]) -> str:
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
