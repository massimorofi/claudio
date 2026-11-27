"""Shared runtime context passed to every tool as its first argument.

Core passes this plain object directly. An engine that needs a framework
wrapper (a context-wrapper type, for instance) builds one around it at the
seam; the tool functions themselves never see the wrapper.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .settings import resolve_profile

if TYPE_CHECKING:
    from .db import Database
    from .settings import AppConfig


@dataclass
class RunConcurrency:
    """Per-run scheduling primitives (issue #58, Increment 1).

    ADK already fans a turn's function calls out concurrently, so the engine
    uses these to keep that concurrency safe: independent read-only calls run
    together under a bounded semaphore, while state-mutating calls are
    serialized behind a single lock so filesystem/DB/counter writes never
    interleave. ``exclusive`` tools (e.g. rewind) take a stricter lock that
    also excludes the ordinary mutations.

    A tool's completion order is non-deterministic under concurrency; because
    every mutation happens under the same lock, the *final* ToolContext state
    is deterministic regardless of that order.
    """

    read_semaphore: asyncio.Semaphore
    mutation_lock: asyncio.Lock
    exclusive_lock: asyncio.Lock
    #: The cap the semaphore was built with, so schedulers can read the bound
    #: back instead of re-deriving it from config (issue #58, Increment 1).
    max_read_concurrency: int = 4

    @classmethod
    def create(cls, max_read_concurrency: int = 4) -> "RunConcurrency":
        cap = max(1, int(max_read_concurrency))
        return cls(
            read_semaphore=asyncio.Semaphore(cap),
            mutation_lock=asyncio.Lock(),
            exclusive_lock=asyncio.Lock(),
            max_read_concurrency=cap,
        )


@dataclass
class ToolContext:
    """Everything tools need at call time. Created per run."""

    config: "AppConfig"
    session_id: str
    workspace: Path
    approval_mode: str  # off | on_request | always
    db: "Database"
    # Per-workspace runtime state root (ADK DBs, file-history). Lives under the
    # backend's own ``data/`` tree, never inside the user's workspace, so
    # Claudio's runtime state cannot pollute a code repo. Computed by the
    # backend from the session's bound ``workspace_id``. When absent (legacy
    # callers, tests) file-history falls back to the old location beside the
    # workspace.
    workspace_id: str = ""
    workspace_state_dir: Path | None = None
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
    # Plan/act audit (see ``claudio_core.planact``). ``called_tools`` records every
    # tool actually invoked this run; ``assistant_text`` accumulates the model's own
    # narration. Together they let the engine detect "said it would use the code
    # graph, never called it" -- a claim about a source that was not consulted.
    called_tools: set[str] = field(default_factory=set)
    assistant_text: list[str] = field(default_factory=list)
    # The live MCP catalog the engine advertises (server id -> real tool names,
    # instructions, descriptions) in the plain-dict shape understood by
    # ``claudio_core.mcp_capability.detect``. When present, the plan/act audit
    # derives one family per connected code-graph server, so a promise to use a
    # specific connector is checked against the tools that connector actually
    # exposes -- and a connector that is not connected is never audited at all.
    # ``None`` (legacy/test callers) leaves the audit on the builtin families.
    mcp_entries: list[dict[str, Any]] | None = None
    # Set by the run manager so tools (e.g. delegate_task) can build agents.
    agent_factory: Any = None
    # Unified call/tool ledger (issue #58, Increment 0). A
    # ``claudio_core.accounting.CallAccountant`` when accounting is wired;
    # ``None`` in legacy/test callers, which simply skip telemetry.
    accountant: Any = None
    # Per-run concurrency primitives (issue #58, Increment 1). ``None`` means
    # no scheduling control (legacy/test callers); the engine sets one per run.
    concurrency: "RunConcurrency | None" = None
    # Canonical turn-boundary tool context (issue #58, Increment 1). Written by
    # ``claudio_core.tool_batch.apply_tool_context``: ``tool_context_text`` is
    # the byte-stable reduction of the last batch, ``tool_context_digest`` its
    # hash, ``tool_context_blocks`` the history of such blocks. Because the
    # reduction sorts by request index and drops timings, two runs whose tools
    # returned the same data produce identical bytes even when the tools
    # completed in different orders.
    tool_context_text: str = ""
    tool_context_digest: str = ""
    tool_context_entries: list[dict[str, Any]] = field(default_factory=list)
    tool_context_blocks: list[str] = field(default_factory=list)

    def record_tool_call(self, name: str) -> None:
        """Note that a tool actually ran, for the plan/act audit."""
        if name:
            self.called_tools.add(name)

    def record_assistant_text(self, text: str) -> None:
        """Keep the model's narration so intent can be audited against actions."""
        if text and text.strip():
            self.assistant_text.append(text)

    def planact_report(self):
        """Audit what the narration promised against what actually ran.

        With a live catalog (``mcp_entries``) the audit runs against the
        per-server families derived from it plus the builtins; without one it
        falls back to the builtin families, which know only generic,
        vendor-free vocabulary.
        """
        from .planact import BUILTIN_FAMILIES, audit_run, derive_families

        text = "\n".join(self.assistant_text)
        if self.mcp_entries:
            families = derive_families(self.mcp_entries) + BUILTIN_FAMILIES
            return audit_run(text, self.called_tools, families)
        return audit_run(text, self.called_tools)

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
    cached_input_tokens: int = 0,
    cache_hit_ratio: float = 0.0,
) -> dict[str, Any]:
    """Compute the ``context_usage`` payload from a used-token count.

    ``free`` is measured against the *usable* window (``window - reserve``),
    since the reserve is held back for the model's response. ``auto_disabled``
    reflects the compaction circuit-breaker state so the gauge can show
    "auto off".

    The window is taken from the ``fast`` profile, not ``agent.context_window``:
    compaction is performed by the fast-profile summariser, so the gauge must
    report the threshold that actually triggers it. When no fast profile is
    configured, :func:`resolve_profile` falls back to the ``llm`` section with
    ``agent.context_window``, so an unprofiled config is unchanged.

    ``cached_input_tokens`` / ``cache_hit_ratio`` (issue #58, Increment 0)
    carry the provider-reported prompt-cache signal for the turn so the gauge
    can show cache effectiveness; both are 0 when the provider reports no
    cache details.
    """
    window = int(resolve_profile(config, "fast").context_window)
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
        "cached_input_tokens": int(cached_input_tokens or 0),
        "cache_hit_ratio": round(float(cache_hit_ratio or 0.0), 4),
    }
