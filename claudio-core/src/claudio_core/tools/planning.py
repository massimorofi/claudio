"""Planning scaffolding: todo_write tool + turn-based nag (Claudio §4.1)."""
from __future__ import annotations

import json

from ..context import ToolContext

# Turns without a todo update before the harness nags (Claudio §4.1). Kept
# short so the plan the user watches ticks along at roughly one update per step.
# This was 10 when the nag was computed once at run start and never actually
# fired; now that the instruction is re-evaluated every turn the value is live,
# so it is tuned for visible progress rather than left at its original guess.
TURNS_SINCE_WRITE_NAG = 4


async def todo_write(
    ctx: ToolContext,
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
    # Reset the in-memory counter too, not just the DB row: the todo nag is
    # computed from ``ctx.turns_since_todo`` on every turn (see
    # ``instructions.build_instructions``), so leaving the live value stale
    # would keep nagging even though the list was just updated.
    ctx.turns_since_todo = 0
    await ctx.db.update_session(ctx.session_id, todos=json.dumps(normalized), turns_since_todo=0)
    return {"ok": True, "count": len(normalized), "items": normalized}
