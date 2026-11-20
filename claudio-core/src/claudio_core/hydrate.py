"""Pre-run deterministic context hydration (issue #58, Increment 2).

When a user prompt names a workspace file explicitly ("fix the bug in
app.py", "what does config_loader.py do?"), the agent normally spends its
first turn reading that file before it can reason about it. This module
detects those explicit, unambiguous references and pre-reads the verified
existing files before the first model call, so turn 1 starts with the content
already in context -- one fewer LLM round-trip on the most common opener.

It is deliberately conservative: only tokens that resolve to an existing file
inside the workspace are read, the total budget is bounded, and the original
user text is preserved (the hydration is appended as a clearly-marked block).
Ambiguous natural language is left entirely to the model -- this is structured
path detection, not intent guessing.
"""
from __future__ import annotations

import re
from pathlib import Path

# A path-like token: something with a slash or a file extension, not a bare word.
_PATH_TOKEN = re.compile(
    r"(?<![\w/.\-])"
    r"((?:[\w.\-]+/)*[\w.\-]+\.[A-Za-z][A-Za-z0-9]{0,6}|[\w.\-]+/)"
    r"(?![\w/.\-])"
)
MAX_HYDRATED_FILES = 4
MAX_PER_FILE_CHARS = 8000
MAX_TOTAL_CHARS = 20000


def detect_explicit_paths(message: str, workspace: Path) -> list[str]:
    """Return workspace-relative paths named in ``message`` that exist.

    Only existing files inside the workspace are returned; a token that does
    not resolve to a real file is ignored (no guessing).
    """
    found: list[str] = []
    seen: set[str] = set()
    try:
        ws = workspace.resolve()
    except OSError:
        return []
    for match in _PATH_TOKEN.finditer(message):
        token = match.group(1)
        candidate = (ws / token)
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        try:
            resolved.relative_to(ws)
        except ValueError:
            continue  # outside the workspace
        rel = str(resolved.relative_to(ws))
        if rel not in seen:
            seen.add(rel)
            found.append(rel)
        if len(found) >= MAX_HYDRATED_FILES:
            break
    return found


def prehydrate(
    message: str, workspace: Path
) -> tuple[str, list[str]]:
    """Return ``(model_message, hydrated_paths)``.

    ``model_message`` is the original message plus a bounded block of the
    explicitly-referenced files' contents. When nothing qualifies, the message
    is returned unchanged and the list is empty (zero cost on the common path).
    """
    paths = detect_explicit_paths(message, workspace)
    if not paths:
        return message, []
    try:
        ws = workspace.resolve()
    except OSError:
        return message, []
    blocks: list[str] = []
    used = 0
    hydrated: list[str] = []
    for rel in paths:
        remaining = MAX_TOTAL_CHARS - used
        if remaining <= 0:
            break
        try:
            text = (ws / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        budget = min(remaining, MAX_PER_FILE_CHARS)
        clipped = text[:budget]
        if len(text) > budget:
            clipped += f"\n[... {len(text) - budget} more chars; read_file for the rest]"
        used += len(clipped)
        hydrated.append(rel)
        blocks.append(f"--- {rel} ---\n{clipped}")
    if not blocks:
        return message, []
    model_message = (
        message
        + "\n\n[Pre-loaded context: the files explicitly referenced above were "
        "read deterministically before this turn, so you do not need to re-read "
        "them.]\n\n"
        + "\n\n".join(blocks)
    )
    return model_message, hydrated


__all__ = ["detect_explicit_paths", "prehydrate"]
