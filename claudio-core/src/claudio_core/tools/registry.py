"""Tool registry: metadata (read-only / destructive / approval) and selection.

The framework-native tool objects (ADK ``FunctionNode``, and so on) are built
by the engine. What lives here is the Claudio semantics a framework does not
model: which tools are read-only, which are destructive, and the approval
policy that decides when a call must pause for a human.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from .files import read_file, write_file, edit_file, list_dir, rewind_files, batch_read
from .search import search_with_context, locate_symbol
from .shell import run_command
from .web import web_fetch, web_search
from .planning import todo_write
from .tasks import create_scheduled_task
from .subagent import delegate_task


@dataclass
class ToolMeta:
    name: str
    is_read_only: bool = False
    is_destructive: bool = False
    # Name of the argument holding a filesystem path, if any (for out-of-workspace checks).
    path_arg: str | None = None
    # Concurrency class (issue #58, Increment 1). ADK already runs a turn's
    # function calls concurrently, so the engine uses this to decide how to
    # schedule each call:
    #   "read_only"      -- safe to run concurrently (bounded by a semaphore).
    #   "state_mutating" -- must be serialized (filesystem/DB/ctx writes).
    #   "exclusive"      -- holds a global per-run lock (e.g. rewind).
    concurrency: str = "read_only"


# Metadata for every built-in tool.
TOOL_META: dict[str, ToolMeta] = {
    "read_file": ToolMeta("read_file", is_read_only=True, path_arg="path", concurrency="read_only"),
    "write_file": ToolMeta("write_file", is_destructive=True, path_arg="path", concurrency="state_mutating"),
    "edit_file": ToolMeta("edit_file", is_destructive=True, path_arg="path", concurrency="state_mutating"),
    "list_dir": ToolMeta("list_dir", is_read_only=True, path_arg="path", concurrency="read_only"),
    # Compound read-only tools (issue #58 Inc 2): one call replaces a
    # grep-then-read or multi-read sequence, so no extra LLM turn is spent.
    "batch_read": ToolMeta("batch_read", is_read_only=True, concurrency="read_only"),
    "search_with_context": ToolMeta("search_with_context", is_read_only=True, concurrency="read_only"),
    "locate_symbol": ToolMeta("locate_symbol", is_read_only=True, concurrency="read_only"),
    # Rewind rewrites many files at once -- serialize it against every other
    # filesystem mutation.
    "rewind_files": ToolMeta("rewind_files", is_destructive=True, concurrency="exclusive"),
    "run_command": ToolMeta("run_command", is_destructive=True, concurrency="state_mutating"),
    "web_fetch": ToolMeta("web_fetch", is_read_only=True, concurrency="read_only"),
    "web_search": ToolMeta("web_search", is_read_only=True, concurrency="read_only"),
    # todo_write is read-only in spirit but mutates the run's ctx counter and the
    # session row, so it is serialized with the other state mutations.
    "todo_write": ToolMeta("todo_write", is_read_only=True, concurrency="state_mutating"),
    # Creating a task row is not itself a system mutation: the task is created
    # disabled + report-only and only acts once the operator enables it.
    "create_scheduled_task": ToolMeta("create_scheduled_task", concurrency="state_mutating"),
    # A delegation runs a worker that can mutate whatever its tool list allows,
    # so delegation is treated as destructive: under ``on_request`` the human
    # approves the work order (the task brief + the tools the worker may use)
    # once, instead of approving each nested tool call inside the worker.
    "delegate_task": ToolMeta("delegate_task", is_destructive=True, concurrency="state_mutating"),
}

ALL_TOOL_NAMES = list(TOOL_META.keys())

# Plain async functions keyed by name. Each takes ToolContext as its first
# argument; the engine wraps them into its own tool representation.
TOOL_FUNCTIONS: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_dir": list_dir,
    "batch_read": batch_read,
    "search_with_context": search_with_context,
    "locate_symbol": locate_symbol,
    "rewind_files": rewind_files,
    "run_command": run_command,
    "web_fetch": web_fetch,
    "web_search": web_search,
    "todo_write": todo_write,
    "create_scheduled_task": create_scheduled_task,
    "delegate_task": delegate_task,
}


def needs_approval_for(meta: ToolMeta, approval_mode: str) -> bool:
    """Decide, statically, whether a tool pauses for approval under ``approval_mode``.

    A plain ``bool`` is used rather than a callable on purpose. A callable policy
    invites the framework to inspect the arguments and prove they were unchanged
    after validation; when a model omits an optional parameter (very common with
    local models) that proof fails and the framework demands approval for *every*
    call — including read-only tools like ``web_fetch``. A static bool skips that
    inspection entirely. Out-of-workspace file access is enforced in the file
    tools themselves, not here.
    """
    if approval_mode == "off":
        return False
    if approval_mode == "always":
        return True
    # on_request: only destructive tools pause for approval.
    return meta.is_destructive


def select_tool_names(
    enabled: list[str],
    disabled: list[str] | None = None,
) -> list[str]:
    """Resolve the effective tool list: ``enabled`` minus ``disabled``.

    Names that aren't registered are dropped silently; the caller logs the
    difference so a typo in config is visible without breaking the run.
    """
    blocked = set(disabled or [])
    return [n for n in enabled if n in TOOL_FUNCTIONS and n not in blocked]


def tool_inventory() -> list[dict[str, Any]]:
    """Full inventory for the frontend (GET /agent/tools)."""
    return [
        {
            "name": meta.name,
            "is_read_only": meta.is_read_only,
            "is_destructive": meta.is_destructive,
        }
        for meta in TOOL_META.values()
    ]


__all__ = [
    "ToolMeta",
    "TOOL_META",
    "ALL_TOOL_NAMES",
    "TOOL_FUNCTIONS",
    "needs_approval_for",
    "select_tool_names",
    "tool_inventory",
]
