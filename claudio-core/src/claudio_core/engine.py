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
    price_input_per_mtok: float = 0.0
    price_output_per_mtok: float = 0.0
    # The model's real context window. The engine needs it to keep every
    # request under the hard limit (the context guard truncates the prompt to
    # fit); without it the only overflow defence is the reactive one, which
    # cannot help when a single large tool result jumps the prompt past the
    # ceiling between two proactive checks.
    context_window: int = 32768
    # Reasoning-effort level in effect for calls made with this spec
    # ("" = provider default). Recorded by the call ledger (issue #58
    # Increment 0); phase-aware effort scaling (Increment 5) sets it.
    reasoning_effort: str = ""


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
    # Registry id of the workspace this run operates on. Drives where Claudio
    # keeps its own per-workspace runtime state (ADK DBs, file-history), so
    # that state never lands inside the user's workspace.
    workspace_id: str = ""
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


@dataclass(frozen=True)
class EngineCapabilities:
    """What an engine implementation can do, for diagnostics and validation.

    The run manager and the ``/version`` endpoint read these to decide which
    Claudio features are available for the selected engine, instead of assuming
    the ADK implementation's abilities. An engine that cannot honour a capability
    must declare it false so the surface degrades honestly rather than
    fabricating success.
    """

    streaming: bool = True
    approvals: bool = True
    tools: bool = True
    compaction: bool = True
    resume_after_restart: bool = True
    usage_reporting: bool = True


@dataclass(frozen=True)
class EngineDescriptor:
    """Stable identity for a selected engine (issue #57)."""

    id: str
    version: str = "unknown"
    capabilities: EngineCapabilities = field(default_factory=EngineCapabilities)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "capabilities": {
                k: v
                for k, v in self.capabilities.__dict__.items()
            },
        }


#: The checkpoint envelope schema version. Bump when the envelope shape changes.
CHECKPOINT_SCHEMA_VERSION = 1


class CheckpointError(Exception):
    """A run checkpoint could not be validated for the selected engine."""


@dataclass
class EngineCheckpoint:
    """An opaque, versioned, engine-tagged resume checkpoint (issue #57).

    ``RunManager`` persists and returns this structure *without interpreting*
    ``payload`` -- the payload is owned by the engine that produced it. The
    envelope carries the producing engine id and a schema version so the
    backend can reject an incompatible checkpoint explicitly instead of trying
    to resume a run against the wrong engine.
    """

    engine: str
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "schema_version": self.schema_version,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "EngineCheckpoint":
        if not isinstance(data, dict):
            raise CheckpointError("checkpoint must be a JSON object")
        engine = data.get("engine")
        if not engine:
            raise CheckpointError("checkpoint is missing its engine id")
        version = data.get("schema_version", CHECKPOINT_SCHEMA_VERSION)
        if version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointError(
                f"unsupported checkpoint schema_version {version!r}"
            )
        payload = data.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        return cls(engine=str(engine), payload=payload, schema_version=int(version))

    def validate_for(self, engine_id: str) -> None:
        """Raise :class:`CheckpointError` unless this checkpoint is ours."""
        if self.engine != engine_id:
            raise CheckpointError(
                f"checkpoint was produced by engine {self.engine!r}, "
                f"but the selected engine is {engine_id!r}"
            )


@runtime_checkable
class RunEngine(Protocol):
    """The contract every engine implementation must satisfy.

    Beyond the run operations (``stream``/``resume``/``cancel``/conversation
    access), an engine exposes a lifecycle (``start``/``close``/``reload_config``)
    and identity (``descriptor``/``capabilities``) so the supervisor and API can
    treat every implementation uniformly instead of assuming ADK specifics.
    """

    @property
    def descriptor(self) -> EngineDescriptor:
        """Stable engine id/version/capabilities for diagnostics and /version."""
        ...

    def capabilities(self) -> EngineCapabilities:
        """The features this engine honours (mirrors ``descriptor.capabilities``)."""
        ...

    async def start(self) -> None:
        """Validate the selected binary/config at startup.

        Must raise a clear, actionable error if the engine is unavailable or
        misconfigured; the backend must not silently fall back to another engine.
        """
        ...

    async def close(self) -> None:
        """Stop engine-owned servers/subprocesses and release sessions.

        Safe to call more than once.
        """
        ...

    def reload_config(self, config: Any) -> None:
        """Apply reloadable settings.

        If the change would require a different engine/image, raise
        :class:`EngineError` to reject the hot reload (a restart is required).
        """
        ...

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

    async def get_conversation_items(
        self, session_id: str, workspace_id: str
    ) -> list[dict[str, Any]]:
        """Read the model-facing conversation as OpenAI-style items.

        Returns the conversation as a list of ``{"role", "content"}`` dicts
        (the shape ``claudio_core.compaction`` renders for summarisation).
        An empty list means there is no stored conversation.
        """
        ...

    async def replace_conversation(
        self, session_id: str, workspace_id: str, summary: str
    ) -> None:
        """Replace the model-facing conversation with a single summary turn.

        Used by manual compaction: the long transcript is dropped and the
        summary becomes the only prior context the next run sees.
        """
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
