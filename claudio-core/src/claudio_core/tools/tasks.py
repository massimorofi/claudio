"""Chat-side task creation: let Claudio create scheduled tasks under user specs.

The operator can ask Claudio in chat to set up a recurring job. The task is
created **disabled and report-only** on purpose: a chat-created task never runs
on its own until the operator reviews it and flips it on in the Autonomous
screen. This keeps a conversational request from silently installing a recurring
mutating job.
"""
from __future__ import annotations

import re

from croniter import croniter

from ..context import ToolContext

# A task key must start with a letter/digit and use only lowercase letters,
# digits and hyphens (matches the API's _KEY_RE).
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _slugify(name: str) -> str:
    """Derive a task-key-safe slug from a human name."""
    slug = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        return "task"
    if not slug[0].isalnum():
        slug = "task-" + slug
    return slug[:64]


async def create_scheduled_task(
    ctx: ToolContext,
    name: str,
    schedule: str,
    prompt: str = "",
    script: str = "",
    description: str = "",
) -> dict:
    """Create a scheduled task the operator can review and enable later.

    Use this when the user asks to set up a recurring job (a cron schedule).
    Two kinds of task are supported:

      * a **prompt task** — a playbook the agent runs each time (pass ``prompt``);
      * a **script task** — a shell script run directly (pass ``script``).

    The task is created **disabled and report-only**: it will not run until the
    operator reviews it and enables it in the Autonomous screen. Tell the user
    that after creating it.

    Args:
        name: Human-readable task name (e.g. "Nightly backup check").
        schedule: A 5-field cron expression, e.g. "0 3 * * *" for 3am daily.
        prompt: Instructions for a prompt-type task. Required unless ``script`` is given.
        script: A shell script for a script-type task. If non-empty, the task is a
            script task and ``prompt`` is ignored.
        description: A short one-line summary of what the task does.
    """
    if not croniter.is_valid(schedule):
        return {"ok": False, "error": f"invalid cron schedule: {schedule!r}"}

    has_script = bool(script and script.strip())
    has_prompt = bool(prompt and prompt.strip())
    if has_script:
        task_type = "script"
        content = script
    elif has_prompt:
        task_type = "prompt"
        content = prompt
    else:
        return {
            "ok": False,
            "error": "provide a prompt (playbook task) or a script (shell task)",
        }

    # Fold the description into the prompt as a leading summary so it is not
    # lost; a script task keeps its script verbatim.
    if task_type == "prompt" and description and description.strip():
        content = f"{description.strip()}\n\n{content}"

    db = ctx.db
    base = _slugify(name)
    key = base
    suffix = 2
    while await db.get_task(key) is not None:
        key = f"{base}-{suffix}"
        suffix += 1
        if suffix > 100:
            return {"ok": False, "error": "could not derive a unique task key"}
    if not _KEY_RE.match(key):
        return {"ok": False, "error": f"derived invalid task key: {key!r}"}

    task = await db.create_task(
        key=key,
        name=name,
        prompt=content if task_type == "prompt" else "",
        script=content if task_type == "script" else None,
        schedule=schedule,
        report_only=True,
        enabled=False,
        notify_on=["warning", "critical", "failure"],
        type=task_type,
    )
    await db.add_audit(
        action="task_created_via_chat",
        command=key,
        detail=f"{task_type}: {name}",
        actor="agent",
    )
    return {
        "ok": True,
        "key": key,
        "type": task_type,
        "message": (
            f"Created {task_type} task '{name}' (key {key}) on schedule "
            f"'{schedule}'. It is disabled and report-only — review and enable "
            "it in the Autonomous screen to start it running."
        ),
    }
