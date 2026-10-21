"""Shared runtime context passed to every tool as its first argument.

Core passes this plain object directly. An engine that needs a framework
wrapper (a context-wrapper type, for instance) builds one around it at the
seam; the tool functions themselves never see the wrapper.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .db import Database
    from .settings import AppConfig


@dataclass
class ToolContext:
    """Everything tools need at call time. Created per run."""

    config: "AppConfig"
    session_id: str
    workspace: Path
    approval_mode: str  # off | on_request | always
    db: "Database"
    # Mutable per-run accumulators.
    run_id: str = ""
    cost_usd: float = 0.0
    turns_since_todo: int = 0
    # Autonomy tracking (see §3.6.14). ``deliverable_written`` flips to True the
    # first time any file-producing tool succeeds this run; ``reads_since_write``
    # counts read/lookup calls made since the last write. Together they drive the
    # write-first nudge that counteracts the "read forever, never produce"
    # failure mode: a small model that explores endlessly and runs out of turns
    # without ever creating the deliverable.
    deliverable_written: bool = False
    reads_since_write: int = 0
    # Set by the run manager so tools (e.g. spawn_subagent) can build agents.
    agent_factory: Any = None

    @property
    def max_output_chars(self) -> int:
        return self.config.agent.max_output_chars

    def resolve_path(self, path: str) -> Path:
        """Resolve a path against the workspace; return absolute path."""
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        return p.resolve()

    def is_within_workspace(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.workspace.resolve())
            return True
        except ValueError:
            return False


def context_usage_payload(
    used_tokens: int,
    config: "AppConfig",
    source: str = "estimate",
    auto_disabled: bool = False,
) -> dict[str, Any]:
    """Compute the ``context_usage`` payload from a used-token count.

    ``free`` is measured against the *usable* window (``window - reserve``),
    since the reserve is held back for the model's response. ``auto_disabled``
    reflects the compaction circuit-breaker state so the gauge can show
    "auto off".
    """
    window = int(config.agent.context_window)
    reserve = int(config.agent.compaction_reserve)
    usable = max(1, window - reserve)
    free = max(0, usable - used_tokens)
    percent = round(min(100.0, (used_tokens / usable) * 100.0), 1)
    return {
        "used_tokens": int(used_tokens),
        "context_window": window,
        "usable_tokens": usable,
        "free_tokens": free,
        "percent_used": percent,
        "compact_threshold": usable,
        "source": source,
        "auto_disabled": auto_disabled,
    }
