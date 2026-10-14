"""Task-style subagent spawning (Claudio §4.2, §3.6.8).

The OpenAI SDK's ``handoff`` *transfers* the conversation. Claudio's Task pattern
*spawns a child with a fresh context and keeps the parent*: the child does its work
and only its final report returns to the parent as the tool result. This keeps the
parent's context lean.
"""
from __future__ import annotations

from agents import Runner, RunContextWrapper

from .context import ToolContext


async def spawn_subagent(
    ctx: RunContextWrapper[ToolContext],
    agent_id: str,
    task: str,
) -> dict:
    """Spawn a subagent with a fresh context to handle a self-contained task.

    The subagent runs independently and returns only its final report — its
    intermediate work does not enter your context. Use it for tasks that require
    reading across many files or long research where you only want the conclusion.

    Args:
        agent_id: Which configured subagent to spawn (e.g. "explorer").
        task: A complete, self-contained description of the task. Include all
            context the subagent needs — it cannot see this conversation.
    """
    tctx = ctx.context
    factory = tctx.agent_factory
    if factory is None:
        return {"ok": False, "error": "Subagent factory not available"}

    subagent_cfg = tctx.config.agent.subagents.get(agent_id)
    if subagent_cfg is None:
        available = ", ".join(tctx.config.agent.subagents.keys()) or "(none)"
        return {
            "ok": False,
            "error": f"Unknown subagent '{agent_id}'. Available: {available}",
        }

    subagent = factory(agent_id)
    if subagent is None:
        return {"ok": False, "error": f"Failed to build subagent '{agent_id}'"}

    try:
        result = await Runner.run(
            subagent,
            task,
            max_turns=subagent_cfg.max_turns,
        )
    except Exception as e:  # noqa: BLE001 — subagent failure is data, not fatal
        return {"ok": False, "error": f"Subagent '{agent_id}' failed: {e}"}

    report = result.final_output
    if not isinstance(report, str):
        report = str(report)
    return {"ok": True, "agent": agent_id, "report": report}
