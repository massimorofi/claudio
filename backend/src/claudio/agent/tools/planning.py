"""Planning scaffolding: todo_write tool + turn-based nag (Claudio §4.1)."""
from __future__ import annotations

import json

from agents import RunContextWrapper

from ..context import ToolContext

# Turns without a todo update before the harness nags (Claudio §4.1).
TURNS_SINCE_WRITE_NAG = 10


async def todo_write(
    ctx: RunContextWrapper[ToolContext],
    items: list[dict],
) -> dict:
    """Write the current task list to track progress on multi-step work.

    Each item is an object with:
      - content: short description of the task
      - status: one of "pending", "in_progress", "done"

    Call this when you plan a multi-step task and update it as you progress.

    Args:
        items: The full task list (replaces any previous list).
    """
    tctx = ctx.context
    normalized = []
    for raw in items:
        if not isinstance(raw, dict):
            return {"ok": False, "error": "each todo item must be an object"}
        content = str(raw.get("content", "")).strip()
        status = str(raw.get("status", "pending"))
        if not content:
            return {"ok": False, "error": "todo item missing 'content'"}
        if status not in ("pending", "in_progress", "done"):
            return {"ok": False, "error": f"invalid status: {status}"}
        normalized.append({"content": content, "status": status})
    await tctx.db.update_session(tctx.session_id, todos=json.dumps(normalized), turns_since_todo=0)
    return {"ok": True, "count": len(normalized), "items": normalized}
