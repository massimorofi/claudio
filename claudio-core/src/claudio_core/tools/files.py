"""File tools with per-edit history backups (Claudio §5.4).

Every write/edit backs up the previous content before modifying the file, so
changes can be rewound. Backups live in the per-workspace runtime state dir
(``<workspace_state_dir>/file-history/<session_id>/``), never inside the
user's workspace. File tools are confined to the workspace: in ``on_request``
mode the read-only tools refuse paths that resolve outside it (destructive
tools are gated by approval instead).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..context import ToolContext


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return text[:max_chars] + f"\n[clipped: {omitted} chars omitted]"


def _history_dir(ctx: ToolContext) -> Path:
    # Prefer the per-workspace runtime state dir (under the backend's data
    # tree). Fall back to the legacy location beside the workspace only when
    # the caller did not supply one, so old call sites keep working.
    if ctx.workspace_state_dir is not None:
        d = Path(ctx.workspace_state_dir) / "file-history" / ctx.session_id
    else:
        d = ctx.workspace.parent / "file-history" / ctx.session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_index(ctx: ToolContext) -> dict:
    idx_path = _history_dir(ctx) / "index.json"
    if idx_path.exists():
        try:
            return json.loads(idx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_index(ctx: ToolContext, index: dict) -> None:
    (_history_dir(ctx) / "index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )


def _backup_file(ctx: ToolContext, target: Path) -> str | None:
    """Back up an existing file before modification. Returns backup filename."""
    if not target.exists() or not target.is_file():
        return None
    index = _load_index(ctx)
    key = str(target)
    versions = index.get(key, [])
    version = len(versions) + 1
    safe_name = target.name.replace("/", "_")
    backup_name = f"{safe_name}@v{version}"
    backup_path = _history_dir(ctx) / backup_name
    shutil.copy2(target, backup_path)
    versions.append(backup_name)
    index[key] = versions
    _save_index(ctx, index)
    return backup_name


async def read_file(
    ctx: ToolContext,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict:
    """Read a text file from the workspace.

    Args:
        path: File path (relative to the workspace, or absolute).
        start_line: Optional 1-based first line to read.
        end_line: Optional 1-based last line to read (inclusive).
    """
    target = ctx.resolve_path(path)
    if ctx.approval_mode == "on_request" and not ctx.is_within_workspace(target):
        return {"ok": False, "error": f"Path outside workspace: {path}"}
    if not target.exists():
        return {"ok": False, "error": f"No such file: {path}"}
    if not target.is_file():
        return {"ok": False, "error": f"Not a file: {path}"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"Cannot read {path}: {e}"}
    lines = text.splitlines()
    if start_line is not None or end_line is not None:
        lo = max(1, start_line or 1)
        hi = min(len(lines), end_line or len(lines))
        lines = lines[lo - 1 : hi]
    numbered = "\n".join(f"{i + (start_line or 1)}: {line}" for i, line in enumerate(lines))
    return {"ok": True, "content": _clip(numbered, ctx.max_output_chars)}


async def write_file(
    ctx: ToolContext,
    path: str,
    content: str,
    append: bool = False,
) -> dict:
    """Write (create or overwrite) a text file in the workspace. Previous content is backed up.

    Use ``append=True`` to add ``content`` to the end of an existing file instead
    of overwriting it. This is how a large file should be built: write the first
    section with a normal call, then add each following section with
    ``append=true``. A single write that is too big for one model response gets
    truncated mid-content and fails, so large documents must be written in
    several smaller appends rather than one big write (see §3.6.14).

    Args:
        path: File path (relative to the workspace, or absolute).
        content: The text to write (or, with ``append``, to add).
        append: If true, add ``content`` to the end of the file instead of
            replacing it.
    """
    target = ctx.resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_file(ctx, target)
    try:
        if append:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"Cannot write {path}: {e}"}
    # A successful write is the signal that the agent has produced output: mark
    # the deliverable as written and clear the read-since-write counter so the
    # write-first nudge (see instructions.build_instructions) stops.
    ctx.deliverable_written = True
    ctx.reads_since_write = 0
    try:
        size = target.stat().st_size
    except OSError:
        size = len(content.encode())
    result: dict = {"ok": True, "written": str(target), "bytes": size}
    if backup:
        result["backup"] = backup
    return result


async def edit_file(
    ctx: ToolContext,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict:
    """Replace an exact string in a file. Previous content is backed up.

    Args:
        path: File path (relative to the workspace, or absolute).
        old_string: Exact text to find.
        new_string: Replacement text.
        replace_all: Replace every occurrence instead of requiring a unique match.
    """
    target = ctx.resolve_path(path)
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": f"No such file: {path}"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"Cannot read {path}: {e}"}
    count = text.count(old_string)
    if count == 0:
        return {"ok": False, "error": "old_string not found in file"}
    if count > 1 and not replace_all:
        return {
            "ok": False,
            "error": f"old_string appears {count} times; make it unique or set replace_all=true",
        }
    new_text = text.replace(old_string, new_string) if replace_all else text.replace(
        old_string, new_string, 1
    )
    backup = _backup_file(ctx, target)
    try:
        target.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"Cannot write {path}: {e}"}
    # Editing a file counts as producing output too (see write_file above).
    ctx.deliverable_written = True
    ctx.reads_since_write = 0
    result: dict = {"ok": True, "edited": str(target), "replacements": count if replace_all else 1}
    if backup:
        result["backup"] = backup
    return result


async def list_dir(ctx: ToolContext, path: str = ".") -> dict:
    """List the entries of a directory in the workspace.

    Args:
        path: Directory path (relative to the workspace, or absolute).
    """
    target = ctx.resolve_path(path)
    if ctx.approval_mode == "on_request" and not ctx.is_within_workspace(target):
        return {"ok": False, "error": f"Path outside workspace: {path}"}
    if not target.exists() or not target.is_dir():
        return {"ok": False, "error": f"No such directory: {path}"}
    entries = []
    for child in sorted(target.iterdir()):
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.exists() else 0,
            }
        )
    return {"ok": True, "path": str(target), "entries": entries[:500]}


async def rewind_files(
    ctx: ToolContext, path: str | None = None
) -> dict:
    """Restore file(s) to their last backed-up state (before the most recent edit).

    Args:
        path: Specific file to rewind; omit to rewind every tracked file in the session.
    """
    index = _load_index(ctx)
    if not index:
        return {"ok": False, "error": "No file history for this session"}
    restored = []
    targets: list[str] = []
    if path is not None:
        resolved = str(ctx.resolve_path(path))
        if resolved not in index:
            return {"ok": False, "error": f"No backup recorded for {path}"}
        targets = [resolved]
    else:
        targets = list(index.keys())
    for key in targets:
        versions = index.get(key) or []
        if not versions:
            continue
        backup_path = _history_dir(ctx) / versions[-1]
        if not backup_path.exists():
            continue
        try:
            shutil.copy2(backup_path, key)
            restored.append(key)
        except OSError:
            continue
    return {"ok": True, "restored": restored, "count": len(restored)}
