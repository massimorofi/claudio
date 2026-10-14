"""Shared runtime context passed to every tool via RunContextWrapper.context."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db import Database
    from ..settings import AppConfig


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
