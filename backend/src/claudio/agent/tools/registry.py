"""Tool registry: metadata (read-only / destructive / approval) + enabled-tool building.

The OpenAI Agents SDK models tools as ``FunctionTool`` objects. This registry adds
the Claudio semantics the SDK doesn't model: which tools are read-only, which are
destructive, and the approval policy that decides when a tool call must pause for
human approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agents import FunctionTool, function_tool, RunContextWrapper

from ..context import ToolContext
from .files import read_file, write_file, edit_file, list_dir, rewind_files
from .shell import run_command
from .web import web_fetch, web_search
from .planning import todo_write


@dataclass
class ToolMeta:
    name: str
    is_read_only: bool = False
    is_destructive: bool = False
    # Name of the argument holding a filesystem path, if any (for out-of-workspace checks).
    path_arg: str | None = None


# Metadata for every built-in tool.
TOOL_META: dict[str, ToolMeta] = {
    "read_file": ToolMeta("read_file", is_read_only=True, path_arg="path"),
    "write_file": ToolMeta("write_file", is_destructive=True, path_arg="path"),
    "edit_file": ToolMeta("edit_file", is_destructive=True, path_arg="path"),
    "list_dir": ToolMeta("list_dir", is_read_only=True, path_arg="path"),
    "rewind_files": ToolMeta("rewind_files", is_destructive=True),
    "run_command": ToolMeta("run_command", is_destructive=True),
    "web_fetch": ToolMeta("web_fetch", is_read_only=True),
    "web_search": ToolMeta("web_search", is_read_only=True),
    "todo_write": ToolMeta("todo_write", is_read_only=True),
}

ALL_TOOL_NAMES = list(TOOL_META.keys())

# Raw function objects keyed by name (plain async functions, decorated below).
_RAW_TOOLS: dict[str, Callable[..., Any]] = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_dir": list_dir,
    "rewind_files": rewind_files,
    "run_command": run_command,
    "web_fetch": web_fetch,
    "web_search": web_search,
    "todo_write": todo_write,
}


def _make_approval_check(meta: ToolMeta) -> Callable[..., Awaitable[bool]]:
    """Build a needs_approval callable that reads the live approval_mode from context."""

    async def _needs(
        context: RunContextWrapper[ToolContext],
        params: dict[str, Any],
        call_id: str,
    ) -> bool:
        mode = context.context.approval_mode
        if mode == "off":
            return False
        if mode == "always":
            return True
        # on_request: gate destructive tools and out-of-workspace file access.
        if meta.is_destructive:
            return True
        if meta.path_arg:
            raw = params.get(meta.path_arg)
            if isinstance(raw, str):
                resolved = context.context.resolve_path(raw)
                if not context.context.is_within_workspace(resolved):
                    return True
        return False

    return _needs


def build_tools(
    enabled: list[str],
    disabled: list[str] | None = None,
    strict_mode: bool = False,
) -> list[FunctionTool]:
    """Build FunctionTool objects for the enabled tool names.

    Tools in ``enabled`` that aren't registered are ignored (logged by the caller).
    ``disabled`` removes tools even if listed in ``enabled``.
    """
    disabled = set(disabled or [])
    tools: list[FunctionTool] = []
    for name in enabled:
        if name not in _RAW_TOOLS:
            continue
        if name in disabled:
            continue
        meta = TOOL_META[name]
        tool = function_tool(
            _RAW_TOOLS[name],
            name_override=name,
            strict_mode=strict_mode,
            needs_approval=_make_approval_check(meta),
        )
        tools.append(tool)
    return tools


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
