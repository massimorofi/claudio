"""Compound search-and-excerpt tools (issue #58, Increment 2).

A model normally stitches two turns to answer "where is X and what does it
look like": a grep-like call, then a read of the hit. These tools collapse that
into one deterministic call that returns matches *with surrounding context*,
eliminating the stitching turn entirely -- no LLM is used to assemble them.

Both are read-only, workspace-confined, and bounded so a broad pattern cannot
dump the whole tree into the prompt.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..context import ToolContext

# Directories never descended into: they are either huge, binary, or irrelevant
# to source search.
_SKIP_DIRS = frozenset(
    {
        ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".idea", ".next",
    }
)
# Only search text-ish files; skip obvious binaries by extension.
_BINARY_EXT = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".zip", ".gz",
        ".tar", ".so", ".dll", ".exe", ".bin", ".woff", ".woff2", ".mp3", ".mp4",
        ".class", ".pyc", ".db", ".sqlite",
    }
)
_MAX_FILE_BYTES = 1_000_000  # skip files larger than 1 MB
_DEFAULT_MAX_MATCHES = 50


def _iter_text_files(root: Path, glob: str | None, limit: int = 4000):
    pattern = glob or "**/*"
    count = 0
    for path in root.glob(pattern):
        if count >= limit:
            return
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in _BINARY_EXT:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        count += 1
        yield path


async def search_with_context(
    ctx: ToolContext,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    context_lines: int = 2,
    max_matches: int = _DEFAULT_MAX_MATCHES,
    regex: bool = False,
    ignore_case: bool = False,
) -> dict:
    """Search the workspace for a text/regex pattern, returning matches with context.

    One call replaces the usual grep-then-read round trip: every hit comes back
    with a few lines of surrounding context and its file:line location, so the
    model can act without a follow-up read.

    Args:
        pattern: Literal substring (default) or regular expression to find.
        path: Directory to search, relative to the workspace (default: root).
        glob: Optional glob to restrict files, e.g. ``**/*.py``.
        context_lines: Lines of context to include before and after each match.
        max_matches: Stop after this many matches across all files.
        regex: Treat ``pattern`` as a Python regular expression.
        ignore_case: Case-insensitive matching.
    """
    root = ctx.resolve_path(path)
    if ctx.approval_mode == "on_request" and not ctx.is_within_workspace(root):
        return {"ok": False, "error": f"Path outside workspace: {path}"}
    if not root.exists():
        return {"ok": False, "error": f"No such directory: {path}"}

    flags = re.IGNORECASE if ignore_case else 0
    try:
        expr = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as exc:
        return {"ok": False, "error": f"Invalid regex: {exc}"}

    context_lines = max(0, min(int(context_lines), 10))
    max_matches = max(1, min(int(max_matches), 200))

    matches: list[dict] = []
    truncated = False
    for file_path in _iter_text_files(root, glob):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if not expr.search(line):
                continue
            lo = max(0, idx - context_lines)
            hi = min(len(lines), idx + context_lines + 1)
            excerpt = "\n".join(
                f"{n + 1}: {lines[n]}" for n in range(lo, hi)
            )
            try:
                rel = str(file_path.relative_to(ctx.workspace))
            except ValueError:
                rel = str(file_path)
            matches.append({"file": rel, "line": idx + 1, "excerpt": excerpt})
            if len(matches) >= max_matches:
                truncated = True
                break
        if truncated:
            break

    return {
        "ok": True,
        "pattern": pattern,
        "match_count": len(matches),
        "truncated": truncated,
        "matches": matches,
    }


async def locate_symbol(
    ctx: ToolContext,
    symbol: str,
    path: str = ".",
    glob: str | None = None,
    max_matches: int = 30,
) -> dict:
    """Find where a symbol is defined (def/class/function/etc.) with context.

    A deterministic structural lookup: it matches common definition forms
    (``def NAME``, ``class NAME``, ``function NAME``, ``NAME =``, method/field
    declarations) rather than every textual occurrence, so the result points at
    definitions instead of call sites.

    Args:
        symbol: The identifier to locate.
        path: Directory to search, relative to the workspace.
        glob: Optional glob to restrict files.
        max_matches: Stop after this many definitions.
    """
    root = ctx.resolve_path(path)
    if ctx.approval_mode == "on_request" and not ctx.is_within_workspace(root):
        return {"ok": False, "error": f"Path outside workspace: {path}"}
    if not root.exists():
        return {"ok": False, "error": f"No such directory: {path}"}

    esc = re.escape(symbol)
    # Definition-ish anchors across the common languages Claudio edits.
    def_expr = re.compile(
        rf"(^\s*(def|class|function|func|struct|interface|enum|type)\s+{esc}\b)"
        rf"|(^\s*(?:async\s+)?def\s+{esc}\b)"
        rf"|(^\s*{esc}\s*[:=])"
        rf"|(^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], .]+\s+{esc}\s*\()"
    )
    max_matches = max(1, min(int(max_matches), 100))

    matches: list[dict] = []
    for file_path in _iter_text_files(root, glob):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if not def_expr.search(line):
                continue
            lo = max(0, idx)
            hi = min(len(lines), idx + 3)
            excerpt = "\n".join(f"{n + 1}: {lines[n]}" for n in range(lo, hi))
            try:
                rel = str(file_path.relative_to(ctx.workspace))
            except ValueError:
                rel = str(file_path)
            matches.append({"file": rel, "line": idx + 1, "excerpt": excerpt})
            if len(matches) >= max_matches:
                break
        if len(matches) >= max_matches:
            break

    return {
        "ok": True,
        "symbol": symbol,
        "definition_count": len(matches),
        "definitions": matches,
    }


__all__ = ["search_with_context", "locate_symbol"]
