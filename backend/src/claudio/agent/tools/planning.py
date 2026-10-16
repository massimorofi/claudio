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
    # Local models frequently hand this tool a JSON *string* instead of a list,
    # or items with the text under a different key. Be permissive: coerce what we
    # can, skip what we can't, and never hard-fail the turn over malformed
    # scaffolding — a dropped todo line is far less harmful than a failed step.
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError):
            items = []
    if not isinstance(items, list):
        items = []
    normalized = []
    for raw in items:
        if isinstance(raw, str):
            content = raw.strip()
            status = "pending"
        elif isinstance(raw, dict):
            content = ""
            for key in ("content", "title", "task", "text", "description"):
                val = raw.get(key)
                if isinstance(val, str) and val.strip():
                    content = val.strip()
                    break
            status = str(raw.get("status", "pending"))
        else:
            continue
        if not content:
            continue
        if status not in ("pending", "in_progress", "done"):
            status = "pending"
        normalized.append({"content": content, "status": status})
    await tctx.db.update_session(tctx.session_id, todos=json.dumps(normalized), turns_since_todo=0)
    return {"ok": True, "count": len(normalized), "items": normalized}
