"""System prompt assembly (Claudio §3).

Combines: base identity + environment block + discovered memory files + the
todo-nag reminder when the session has gone too long without a todo update.
"""
from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path

from typing import Any

from .settings import AppConfig
from .memory import discover_memory_files, render_memory_block
from .skill_loader import render_skill_block
from .tools.planning import TURNS_SINCE_WRITE_NAG

# Read/lookup calls made without producing any file before the write-first nudge
# fires. Sized well below the typical task length so it interrupts the
# read-forever spiral early (a few reads is normal; a dozen with no output is
# the failure mode). See §3.6.14.
READS_SINCE_WRITE_NAG = 8

BASE_IDENTITY = """You are Claudio, an AI agent that helps the user get work done by using tools.

Work methodically:
- Understand the request before acting. For any multi-step task, start by writing a todo list with `todo_write`, then update item statuses as you complete each step.
- Use the right tool for each step. Read before you write. Verify results.
- When a tool fails, read the error and adapt — do not repeat the same failing action.
- Keep answers concise and grounded in what you actually observed.
- Respect the workspace: do not touch files outside it unless the user explicitly asks."""

# The delivery contract (§3.6.14). This block is always present because the failure
# it prevents — exploring endlessly and never producing the deliverable — is the
# dominant failure mode for this model on open-ended work, and it must be in front
# of the agent from the very first turn, not loaded on demand.
OPERATING_PROCEDURE = """# How to work (delivery contract)

You are often asked to PRODUCE something — a file, a document, an analysis, a
report, code, a summary. The most common way to fail at this is to keep reading
and analyzing and never actually make the thing. Follow these rules:

1. DECIDE THE DELIVERABLE FIRST. Before any tool call, name in one sentence the
   single concrete thing the user wants (the file, the document, the answer). If
   the request is ambiguous, pick the most reasonable interpretation NOW and note
   the assumption. Do not stop to ask when no one is watching.
2. WRITE IT EARLY. Within your first few turns, create the output file with a
   real skeleton — the headings and a sentence or two under each — using what you
   already know, BEFORE doing deep analysis. A draft you can improve beats more
   reading. Never let many turns pass with no file created.
3. WRITE BIG FILES IN SMALL CHUNKS. Never put a whole long document into one
   write_file call — a single response that is too long gets cut off before the
   content finishes and the write fails. Instead: write the first section with a
   normal write_file, then add each following section with
   write_file(path, content, append=true). Keep each single write to roughly
   2000 characters or less. Build the file up over several small appends.
4. ITERATE, DON'T GATHER. Once the draft exists, each turn should IMPROVE the
   file (fill a section, fix something), not just read more. Prefer editing the
   deliverable over opening another file.
5. TIME-BOX EXPLORATION. Do not read file after file without producing. If you
   notice you are only reading, stop and write what you have so far.
6. DON'T EXPAND THE PLAN MID-RUN. Set your todo list once. If you find new work
   later, write it into the deliverable's "Remaining work" section — do not keep
   adding todo items and restarting.
7. VERIFY BEFORE YOU SAY DONE. Before declaring completion, actually check the
   deliverable exists and contains what was asked (read it back or list it).
   "Done" means the artifact is there and complete — not that you understand it.
8. PARTIAL BEATS NOTHING. If you cannot finish, deliver the best partial version
   with a clear "What is done / What remains" section. NEVER end with "I don't
   have enough information" or a non-answer. Always leave a usable artifact.
9. DECIDE, DON'T ASK. When running unsupervised, make reasonable decisions
   yourself and note assumptions rather than pausing to ask questions."""


def build_static_instructions(
    config: AppConfig,
    session_id: str,
    skill_entries: list[dict[str, Any]] | None = None,
) -> str:
    """Assemble the parts of the system prompt that never change during a run.

    Split out from :func:`render_nudges` so an engine can build this once and
    reuse it across every turn, appending only the nudges as the run progresses.
    Keeping the static text first also lets the model cache the prompt prefix.
    """
    workspace = Path(config.agent.workspace_dir).resolve()
    config_dir = workspace.parent.parent if workspace.parent else workspace

    sections: list[str] = [BASE_IDENTITY, OPERATING_PROCEDURE]

    # Environment block.
    env_lines = [
        "# Environment",
        f"- Workspace: {workspace}",
        f"- Platform: {platform.system().lower()}",
        f"- Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"- Approval mode: {config.agent.approval_mode}",
    ]
    sections.append("\n".join(env_lines))

    # Skills catalog (names + descriptions only; full text loads on demand).
    skill_block = render_skill_block(skill_entries)
    if skill_block:
        sections.append(skill_block)

    # Memory files.
    memory_files = discover_memory_files(workspace, Path(config_dir))
    memory_block = render_memory_block(memory_files)
    if memory_block:
        sections.append(memory_block)

    return "\n\n".join(sections)


def render_nudges(
    turns_since_todo: int = 0,
    reads_since_write: int = 0,
    deliverable_written: bool = False,
) -> str:
    """The reminder blocks that change as a run progresses.

    Returns the empty string when there is nothing to say, so a caller can append
    this straight onto the static instruction. These are deliberately kept apart
    from the static text because they must be re-evaluated every turn: a prompt
    assembled once at run start freezes them at their initial (silent) values,
    which is exactly why the todo nag used never fire mid-run.
    """
    blocks: list[str] = []

    # Write-first nudge (§3.6.14): the agent has spent many read/lookup calls and
    # still has produced no file. This is the read-forever spiral — interrupt it
    # with a direct instruction to create the deliverable now. Suppressed once any
    # file has been written, and worded so a genuinely read-only task can ignore
    # it.
    if not deliverable_written and reads_since_write >= READS_SINCE_WRITE_NAG:
        blocks.append(
            "<system-reminder>\n"
            f"You have made {reads_since_write} read/lookup calls and have not "
            "created any output file yet. If this task has a deliverable (a file, "
            "document, analysis, report, or written answer), STOP gathering and "
            "WRITE IT NOW — a first draft with what you already know, which you "
            "can improve next. Do not keep reading. (If the task is genuinely "
            "read-only — a lookup or a question — ignore this and just answer.)\n"
            "</system-reminder>"
        )

    # Todo nag (Claudio §4.1).
    if turns_since_todo >= TURNS_SINCE_WRITE_NAG:
        blocks.append(
            "<system-reminder>\n"
            f"You have gone {turns_since_todo} turns without updating your task "
            "list. Call `todo_write` NOW and mark every step you have actually "
            "finished as `done` — not just the items you originally planned. "
            "If you have completed work but left those items `pending`, the "
            "progress the user sees is wrong. Update the list first (flip "
            "finished items to `done`, set the next item to `in_progress`), "
            "then continue the work.\n"
            "</system-reminder>"
        )

    if not blocks:
        return ""
    return "\n\n" + "\n\n".join(blocks)


def build_instructions(
    config: AppConfig,
    session_id: str,
    turns_since_todo: int = 0,
    skill_entries: list[dict[str, Any]] | None = None,
    reads_since_write: int = 0,
    deliverable_written: bool = False,
) -> str:
    """Assemble the full system prompt for a run.

    Equivalent to the static instruction plus the current nudges. Engines that
    drive a multi-turn loop should prefer :func:`build_static_instructions` with
    :func:`render_nudges` re-evaluated per turn, rather than calling this once
    and freezing the nudges.
    """
    return build_static_instructions(
        config, session_id, skill_entries
    ) + render_nudges(
        turns_since_todo=turns_since_todo,
        reads_since_write=reads_since_write,
        deliverable_written=deliverable_written,
    )
