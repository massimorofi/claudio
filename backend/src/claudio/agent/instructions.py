"""System prompt assembly (Claudio §3).

Combines: base identity + environment block + discovered memory files + the
todo-nag reminder when the session has gone too long without a todo update.
"""
from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path

from ..settings import AppConfig
from .memory import discover_memory_files, render_memory_block
from .tools.planning import TURNS_SINCE_WRITE_NAG

BASE_IDENTITY = """You are Claudio, an AI agent that helps the user get work done by using tools.

Work methodically:
- Understand the request before acting. For multi-step work, plan with the todo_write tool.
- Use the right tool for each step. Read before you write. Verify results.
- When a tool fails, read the error and adapt — do not repeat the same failing action.
- Keep answers concise and grounded in what you actually observed.
- Respect the workspace: do not touch files outside it unless the user explicitly asks."""


def build_instructions(
    config: AppConfig,
    session_id: str,
    turns_since_todo: int = 0,
) -> str:
    """Assemble the full system prompt for a run."""
    workspace = Path(config.agent.workspace_dir).resolve()
    config_dir = workspace.parent.parent if workspace.parent else workspace

    sections: list[str] = [BASE_IDENTITY]

    # Environment block.
    env_lines = [
        "# Environment",
        f"- Workspace: {workspace}",
        f"- Platform: {platform.system().lower()}",
        f"- Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"- Approval mode: {config.agent.approval_mode}",
    ]
    sections.append("\n".join(env_lines))

    # Memory files.
    memory_files = discover_memory_files(workspace, Path(config_dir))
    memory_block = render_memory_block(memory_files)
    if memory_block:
        sections.append(memory_block)

    # Todo nag (Claudio §4.1).
    if turns_since_todo >= TURNS_SINCE_WRITE_NAG:
        sections.append(
            "<system-reminder>\n"
            "It has been a while since you updated your task list. "
            "Consider using the todo_write tool to track your progress.\n"
            "</system-reminder>"
        )

    return "\n\n".join(sections)
