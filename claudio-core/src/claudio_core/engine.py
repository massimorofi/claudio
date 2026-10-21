"""The framework-neutral engine contract.

``claudio_core`` defines what an agent engine must do; ``claudio-backend``
supplies an implementation (Google ADK). Keeping this interface in core — and
keeping the ADK import on the other side of it — is what makes the engine
swappable and, more importantly, what keeps core testable without any framework
installed.

An engine is a *streaming* thing: you hand it a :class:`RunRequest` and it
yields the Claudio SSE events defined in :mod:`claudio_core.events`. All the
lifecycle concerns that are not about the model (budgets, cancellation,
reconnection, persistence) live in core's ``RunManager``, not here.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .events import EventName


@dataclass(frozen=True)
class ModelSpec:
    """How to reach the inference endpoint.

    Deliberately a plain description rather than a client object: core must not
    construct or hold a framework/model client, only describe what to build.
    """

    model: str
    base_url: str | None = None
    api_key: str = "local"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_seconds: float = 120.0
    fallback_model: str | None = None


@dataclass(frozen=True)
class ToolPolicy:
    """Which tools exist and when they must pause for approval."""

    enabled: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    approval_mode: str = "on_request"  # off | on_request | always
    strict_mode: bool = False


@dataclass(frozen=True)
class RunRequest:
    """Everything needed to drive one agent run.

    ``instructions`` is already fully assembled by core
    (``claudio_core.instructions``) — the engine must not add to it, so the
    prompt is identical regardless of which engine is in play.
    """

    run_id: str
    session_id: str
    workspace: Path
    instructions: str
    user_input: str
    model: ModelSpec
    tools: ToolPolicy
    max_turns: int = 30
    # Prior conversation to seed the engine with, as OpenAI-style message
    # dicts. Empty for a first run; populated after a compaction or when an
    # engine must restart from a summarised state.
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunOutcome:
    """What the engine reports when a run finishes normally.

    Failure is signalled by raising :class:`EngineError`, not by this type; a
    returned outcome means the agent reached a final answer.
    """

    final_output: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    turns_used: int = 0
    raw: Any = None  # engine-native result, for debugging only


class EngineError(Exception):
    """A failure the engine wants surfaced to the operator.

    ``code`` and ``retryable`` map directly onto the ``run_failed`` event, so
    the recovery loop in core can decide what to do without inspecting the
    engine's exception types.
    """

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@runtime_checkable
class RunEngine(Protocol):
    """The contract every engine implementation must satisfy."""

    async def stream(self, request: RunRequest) -> AsyncIterator[dict[str, Any]]:
        """Drive the agent, yielding Claudio SSE events as they happen.

        Yields dicts of the form ``{"event": <name>, "data": {...}}``. Must
        emit ``run_completed`` exactly once on success. Must not emit
        ``run_started`` — the RunManager owns that, since it also owns the
        run's persistence.
        """
        ...

    async def resume(
        self, request: RunRequest, state: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Continue a run that was paused (e.g. awaiting approval).

        ``state`` is whatever :meth:`stream` yielded in a
        ``approval_required``-adjacent checkpoint; the engine owns its own
        serialization and core only stores it opaquely.
        """
        ...

    async def cancel(self, run_id: str) -> None:
        """Request cooperative cancellation of an in-flight run."""
        ...


def is_terminal(event: dict[str, Any]) -> bool:
    """True for the events that end a run."""
    name = event.get("event")
    return name in {
        EventName.RUN_COMPLETED.value,
        EventName.RUN_FAILED.value,
    }


__all__ = [
    "ModelSpec",
    "ToolPolicy",
    "RunRequest",
    "RunOutcome",
    "EngineError",
    "RunEngine",
    "is_terminal",
]
