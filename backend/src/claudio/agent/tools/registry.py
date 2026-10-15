"""Tool registry: metadata (read-only / destructive / approval) + enabled-tool building.

The OpenAI Agents SDK models tools as ``FunctionTool`` objects. This registry adds
the Claudio semantics the SDK doesn't model: which tools are read-only, which are
destructive, and the approval policy that decides when a tool call must pause for
human approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents import FunctionTool, function_tool

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


def needs_approval_for(meta: ToolMeta, approval_mode: str) -> bool:
    """Decide, statically, whether a tool pauses for approval under ``approval_mode``.

    A plain ``bool`` is used rather than a callable on purpose. The SDK only trusts a
    callable policy when it can prove the tool arguments are byte-for-byte unchanged
    after validation; when a model omits an optional parameter (very common with
    local models) that proof fails and the SDK demands approval for *every* call —
    including read-only tools like ``web_fetch``. A static bool skips that inspection
    entirely. Out-of-workspace file access is enforced in the file tools themselves.
    """
    if approval_mode == "off":
        return False
    if approval_mode == "always":
        return True
    # on_request: only destructive tools pause for approval.
    return meta.is_destructive


def build_tools(
    enabled: list[str],
    disabled: list[str] | None = None,
    strict_mode: bool = False,
    approval_mode: str = "on_request",
) -> list[FunctionTool]:
    """Build FunctionTool objects for the enabled tool names.

    Tools in ``enabled`` that aren't registered are ignored (logged by the caller).
    ``disabled`` removes tools even if listed in ``enabled``. ``approval_mode``
    decides each tool's approval policy (see :func:`needs_approval_for`).
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
            needs_approval=needs_approval_for(meta, approval_mode),
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
