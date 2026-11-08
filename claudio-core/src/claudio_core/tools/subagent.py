"""Delegate a self-contained subtask to a named worker subagent.

The primary model uses this tool to hand a well-scoped, mechanical subtask to a
cheaper worker model running on the ``worker`` profile. The worker runs as a
fresh, stateless agent with its own restricted tool set and turn budget; it sees
nothing but the task brief. The final text it produces is returned as the tool
result the primary reads back.

The actual construction and driving of the sub-agent is engine-specific, so it
is injected as ``ctx.agent_factory`` (set by the engine when it builds the run
context). This module holds only the framework-free contract: validation,
argument handling, and the result shape. Keeping it framework-free means it is
testable without ADK installed.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..context import ToolContext

# The async factory signature the engine must provide on ToolContext.agent_factory:
#   await factory(agent=<name>, task=<brief>) -> {"ok": bool, "output": str, "usage": {...}}
SubagentRunner = Callable[..., Awaitable[dict[str, Any]]]


def _valid_names(config) -> str:
    subagents = getattr(config.agent, "subagents", None) or {}
    return ", ".join(sorted(subagents)) or "(none configured)"


async def delegate_task(ctx: ToolContext, task: str, agent: str) -> dict[str, Any]:
    """Run ``task`` on the named worker ``agent`` and return its output.

    Args:
        task: A self-contained brief. The worker has no conversation history,
            so everything it needs must be in this string.
        agent: The name of a configured ``SubagentConfig``.

    Returns:
        ``{"ok": True, "output": <final text>, "usage": {...}}`` on success,
        or ``{"ok": False, "error": <message>}`` on failure.
    """
    if not task or not str(task).strip():
        return {"ok": False, "error": "task must be a non-empty, self-contained brief"}
    if not agent or not str(agent).strip():
        return {"ok": False, "error": "agent (the subagent name) is required"}

    subagents = getattr(ctx.config.agent, "subagents", None) or {}
    if agent not in subagents:
        return {
            "ok": False,
            "error": f"unknown subagent '{agent}'. Valid subagents: {_valid_names(ctx.config)}",
        }

    factory = getattr(ctx, "agent_factory", None)
    if factory is None:
        return {
            "ok": False,
            "error": "subagent runner is not configured (agent_factory missing on context)",
        }

    try:
        result = await factory(agent=agent, task=task)
    except Exception as exc:  # noqa: BLE001 - surfaced as a tool error, not a crash
        return {"ok": False, "error": f"delegation to '{agent}' failed: {exc}"}

    if not isinstance(result, dict):
        return {"ok": False, "error": "subagent runner returned a non-dict result"}
    return result


__all__ = ["delegate_task", "SubagentRunner"]
