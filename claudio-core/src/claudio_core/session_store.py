"""Session store: the JSONL audit mirror (framework-free half).

Upstream this module also created the OpenAI SDK's ``SQLiteSession`` — the
model-facing conversation store. That half belongs to the engine, which owns
the framework. What is left here is the part that never needed it: the
append-only JSONL transcript that powers ``GET /sessions/{id}/messages`` and
survives even if the engine's store is reset.

The model-facing store is described by :class:`ConversationStore` so core can
reason about it (read items, clear, replace) without knowing who provides it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConversationStore(Protocol):
    """The model-facing conversation history.

    An engine supplies one of these. The item shape is the OpenAI-style
    message dict (``{"role": ..., "content": ...}``), which every engine in
    play can produce; core only reads and replaces, never mutates in place.
    """

    async def get_items(self) -> list[dict[str, Any]]:
        """Return the conversation as a list of message dicts."""
        ...

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        """Append messages to the conversation."""
        ...

    async def clear_session(self) -> None:
        """Drop the conversation."""
        ...


class SessionStore:
    """Append-only JSONL transcript, one file per session."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.sessions_dir = data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _transcript_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def append(self, session_id: str, entry: dict[str, Any]) -> None:
        """Append one event to the JSONL transcript."""
        entry = dict(entry)
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with open(self._transcript_path(session_id), "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def read(self, session_id: str) -> list[dict[str, Any]]:
        path = self._transcript_path(session_id)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def clear(self, session_id: str) -> None:
        path = self._transcript_path(session_id)
        if path.exists():
            path.unlink()

    def delete(self, session_id: str) -> None:
        self.clear(session_id)

    @staticmethod
    def repair_orphans(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure every tool_call has a matching tool_result (Claudio §6.7).

        Any tool_call without a result gets a synthesized error result appended,
        so the rendered transcript is always well-formed.
        """
        result_ids = {
            e.get("call_id")
            for e in entries
            if e.get("type") == "tool_result" and e.get("call_id")
        }
        repaired = list(entries)
        for e in entries:
            if e.get("type") == "tool_call" and e.get("call_id") not in result_ids:
                repaired.append(
                    {
                        "type": "tool_result",
                        "call_id": e.get("call_id"),
                        "name": e.get("name"),
                        "output": "No result recorded (interrupted)",
                        "is_error": True,
                        "synthetic": True,
                    }
                )
        return repaired


__all__ = ["SessionStore", "ConversationStore"]
