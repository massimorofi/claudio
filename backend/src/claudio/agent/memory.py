"""CLAUDE.md-style instruction/memory discovery (Claudio §3).

Discards nothing — it collects project instruction files from the workspace and the
config directory and appends them to the system prompt, size-capped.
"""
from __future__ import annotations

from pathlib import Path

# Files discovered, in order.
MEMORY_FILENAMES = ["CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md"]

# Per-file size cap (bytes).
MAX_FILE_BYTES = 32_000
# Total cap across all memory files.
MAX_TOTAL_BYTES = 64_000


def discover_memory_files(workspace: Path, config_dir: Path) -> list[tuple[str, str]]:
    """Return (path, content) for each discovered instruction file."""
    found: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for base in (workspace, config_dir):
        for fname in MEMORY_FILENAMES:
            candidate = (base / fname).resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                try:
                    text = candidate.read_text(errors="replace")
                except OSError:
                    continue
                if len(text.encode()) > MAX_FILE_BYTES:
                    text = text[:MAX_FILE_BYTES] + "\n[truncated: file exceeds size cap]"
                found.append((str(candidate), text))
    # Enforce total cap.
    total = 0
    capped: list[tuple[str, str]] = []
    for path, text in found:
        size = len(text.encode())
        if total + size > MAX_TOTAL_BYTES:
            break
        total += size
        capped.append((path, text))
    return capped


def render_memory_block(files: list[tuple[str, str]]) -> str:
    """Render discovered memory files into a prompt block."""
    if not files:
        return ""
    parts = ["# Project instructions (memory files)"]
    for path, text in files:
        parts.append(f"\n## {path}\n{text}")
    return "\n".join(parts)
