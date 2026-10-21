"""Compose a model-facing message that includes uploaded file contents.

The user's typed message is kept intact and the extracted text of each attached
file is appended in a clearly delimited block, so the model can tell its own
instructions apart from the attached material. The full files stay on disk in
the workspace, so the agent can read more of any of them with ``read_file``
when the inline excerpt is capped.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .extract import extract_text

# Hard ceiling on the total file text injected into a single request. Sized to
# stay comfortably under the input guardrail's MAX_INPUT_CHARS so a large
# attachment never trips the wire.
MAX_TOTAL_FILE_CHARS = 24_000
# Per-file cap so one big file can't crowd out the others.
MAX_PER_FILE_CHARS = 12_000


def compose_message_with_files(
    message: str,
    files: list[str],
    workspace: Path,
    max_total_chars: int = MAX_TOTAL_FILE_CHARS,
) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(model_message, file_summaries)``.

    ``files`` are workspace-relative paths. Each is read, its text extracted and
    appended to ``message``. Files that cannot be read are reported in the
    summaries and skipped. When the total injected text reaches
    ``max_total_chars`` the remaining files are left out (still on disk).
    """
    if not files:
        return message, []

    workspace = workspace.resolve()
    blocks: list[str] = []
    summaries: list[dict[str, Any]] = []
    remaining = max_total_chars

    for rel in files:
        target = (workspace / rel).resolve()
        try:
            target.relative_to(workspace)
        except ValueError:
            summaries.append({"path": rel, "ok": False, "error": "outside workspace"})
            continue
        if not target.exists() or not target.is_file():
            summaries.append({"path": rel, "ok": False, "error": "not found"})
            continue

        budget = max(0, min(MAX_PER_FILE_CHARS, remaining))
        result = extract_text(target, budget)
        remaining -= len(result.text)

        header = f"### File: {rel}  ({result.kind}, {target.stat().st_size} bytes)"
        if result.note:
            header += f"  — {result.note}"
        if result.text.strip():
            blocks.append(f"{header}\n```\n{result.text}\n```")
        else:
            blocks.append(header)
        summaries.append(
            {
                "path": rel,
                "ok": True,
                "kind": result.kind,
                "chars": result.chars,
                "note": result.note,
            }
        )
        if remaining <= 0:
            break

    if not blocks:
        return message, summaries

    context = "\n\n".join(blocks)
    model_message = (
        f"{message}\n\n"
        "────────────────────────────────────────────\n"
        "Attached files (full copies saved in the workspace — use read_file "
        "for anything not shown in full here):\n\n"
        f"{context}"
    )
    return model_message, summaries
