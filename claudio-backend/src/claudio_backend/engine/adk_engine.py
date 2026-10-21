"""Google ADK implementation of the Claudio ``RunEngine`` contract.

The engine's job is deliberately narrow: take a framework-neutral
:class:`~claudio_core.engine.RunRequest`, drive one ADK agent run, and
translate ADK events into Claudio SSE events. Budgets, cancellation,
reconnection and persistence are the ``RunManager``'s business and live
above this layer.

Three things drove this design; all were discovered by probing ADK 2.12
rather than reading its documentation.

**Tool wrapping.** Every Claudio tool takes a
:class:`~claudio_core.context.ToolContext` as its first argument. ADK builds
its tool declaration from the callable's signature, so a bound ``ctx``
parameter would leak into the schema as a required model-facing argument.
Each tool is therefore wrapped in a closure whose declared signature omits
``ctx``; the context is captured, never declared.

**Approval.** ``require_confirmation=True`` is inert unless the app is built
with :class:`ResumabilityConfig`. When it is, ADK injects a synthetic
``adk_request_confirmation`` function call with its **own** id and pauses.
Resuming requires a ``function_response`` keyed by *that* id -- using the
original tool-call id produces no events at all. The pause state is small
and explicit (see :class:`AdkPauseState`) rather than an opaque blob.

**Text.** ADK delivers text per-response, not token-by-token, so a
``token`` event here is a chunk of model output rather than a streamed
token. The contract permits that; the frontend appends.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.apps._configs import ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

from claudio_core import Database
from claudio_core.context import ToolContext, context_usage_payload
from claudio_core.engine import EngineError, ModelSpec, RunRequest
from claudio_core.events import EventName
from claudio_core.settings import AppConfig
from claudio_core.tools.registry import TOOL_FUNCTIONS, TOOL_META, needs_approval_for

logger = logging.getLogger(__name__)

CONFIRMATION_TOOL = "adk_request_confirmation"


# --------------------------------------------------------------------------
# Pause state
# --------------------------------------------------------------------------


@dataclass
class AdkPauseState:
    """Everything needed to resume a run paused on a tool confirmation.

    ADK persists the conversation itself, so the only thing to carry across
    the pause is the id of the synthetic confirmation call plus what was
    being asked. ``approved``/``rejection_reason`` are filled in by the
    RunManager when the human answers.
    """

    confirmation_id: str
    original_call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    hint: str = ""
    approved: bool = True
    rejection_reason: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "kind": "adk_confirmation",
                "confirmation_id": self.confirmation_id,
                "original_call_id": self.original_call_id,
                "tool_name": self.tool_name,
                "arguments": self.arguments,
                "hint": self.hint,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "AdkPauseState":
        data = json.loads(raw)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AdkPauseState":
        if data.get("kind") != "adk_confirmation":
            raise EngineError(
                "bad_resume_state",
                f"Pause state is not an ADK confirmation (got {data.get('kind')!r})",
                retryable=False,
            )
        return cls(
            confirmation_id=data["confirmation_id"],
            original_call_id=data.get("original_call_id", "") or "",
            tool_name=data.get("tool_name", "") or "",
            arguments=data.get("arguments", {}) or {},
            hint=data.get("hint", "") or "",
            approved=bool(data.get("approved", True)),
            rejection_reason=data.get("rejection_reason", "") or "",
        )


def normalise_state(state: Any) -> AdkPauseState:
    """Accept the pause state in any of the shapes it round-trips through."""
    if isinstance(state, AdkPauseState):
        return state
    if isinstance(state, str):
        return AdkPauseState.from_json(state)
    if isinstance(state, dict):
        return AdkPauseState.from_dict(state)
    raise EngineError("bad_resume_state", f"Unsupported resume state {state!r}")


# --------------------------------------------------------------------------
# Tool wrapping
# --------------------------------------------------------------------------


def make_adk_tool(
    name: str,
    ctx: ToolContext,
    *,
    require_confirmation: bool,
) -> FunctionTool:
    """Wrap a core tool into an ADK ``FunctionTool`` with ``ctx`` hidden.

    The wrapper accepts ``**kwargs`` and is then given a synthetic
    ``__signature__`` listing only the model-facing parameters, so ADK
    builds the real schema while ``ctx`` stays invisible to the model.
    """
    fn = TOOL_FUNCTIONS[name]

    real = inspect.signature(fn)
    model_params = [p for p in real.parameters.values() if p.name != "ctx"]

    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(ctx, **kwargs)
        except Exception as exc:  # noqa: BLE001 - tool errors are data, not crashes
            logger.warning("tool %s raised: %s", name, exc)
            return {"error": str(exc)}

    wrapper.__name__ = name
    wrapper.__doc__ = fn.__doc__ or f"{name}."
    wrapper.__signature__ = inspect.Signature(parameters=model_params)

    return FunctionTool(wrapper, require_confirmation=require_confirmation)


# --------------------------------------------------------------------------
# Event translation
# --------------------------------------------------------------------------


def _json_arg(value: Any) -> str:
    """Serialise tool-call arguments as a JSON string.

    The wire contract requires a string here; the React renderer crashes if
    it is handed an object.
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _is_error_response(fresp: Any) -> bool:
    resp = getattr(fresp, "response", None)
    if isinstance(resp, dict):
        if resp.get("error"):
            return True
        if resp.get("ok") is False:
            return True
    return False


def translate_event(event: Any) -> list[dict[str, Any]]:
    """Turn one ADK event into zero or more Claudio SSE events.

    ADK mixes function calls, function responses and text within a single
    event, so this fans out rather than mapping one-to-one.
    """
    out: list[dict[str, Any]] = []
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return out

    author = getattr(event, "author", "") or ""
    is_model = author not in ("", "user")

    for part in content.parts:
        fcall = getattr(part, "function_call", None)
        if fcall is not None:
            name = getattr(fcall, "name", "") or ""
            if name == CONFIRMATION_TOOL:
                # Surfaced as approval_required by the driver, which also
                # captures the pause state; skip to avoid duplicating it.
                continue
            out.append(
                {
                    "event": EventName.TOOL_CALL.value,
                    "data": {
                        "call_id": getattr(fcall, "id", "") or "",
                        "name": name,
                        "arguments": _json_arg(getattr(fcall, "args", None) or {}),
                    },
                }
            )
            continue

        fresp = getattr(part, "function_response", None)
        if fresp is not None:
            name = getattr(fresp, "name", "") or ""
            if name == CONFIRMATION_TOOL:
                continue
            out.append(
                {
                    "event": EventName.TOOL_RESULT.value,
                    "data": {
                        "call_id": getattr(fresp, "id", "") or "",
                        "name": name,
                        "output": _stringify(getattr(fresp, "response", None)),
                        "is_error": _is_error_response(fresp),
                    },
                }
            )
            continue

        text = getattr(part, "text", None) or ""
        if text:
            if getattr(part, "thought", False):
                out.append({"event": EventName.REASONING.value, "data": {"text": text}})
            elif is_model:
                out.append({"event": EventName.TOKEN.value, "data": {"text": text}})

    return out


def find_confirmation(event: Any) -> AdkPauseState | None:
    """Extract the pause state if this event carries a confirmation request."""
    for fcv in event.get_function_calls() or []:
        if getattr(fcv, "name", "") != CONFIRMATION_TOOL:
            continue
        args = getattr(fcv, "args", None) or {}
        original = args.get("originalFunctionCall") or {}
        conf = args.get("toolConfirmation") or {}
        return AdkPauseState(
            confirmation_id=getattr(fcv, "id", "") or "",
            original_call_id=original.get("id", "") or "",
            tool_name=original.get("name", "") or "",
            arguments=original.get("args") or {},
            hint=conf.get("hint", "") or "",
        )
    return None


def usage_from_event(event: Any) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an ADK event, or zeros."""
    usage = getattr(event, "usage_metadata", None)
    if not usage:
        return 0, 0
    tin = getattr(usage, "prompt_token_count", None)
    if tin is None:
        tin = getattr(usage, "input_tokens", 0)
    tout = getattr(usage, "candidates_token_count", None)
    if tout is None:
        tout = getattr(usage, "output_tokens", 0)
    return int(tin or 0), int(tout or 0)


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


def adk_state_path(request: RunRequest) -> Path:
    """Where ADK keeps its own session state for this Claudio session.

    The parent directory is created here because the engine owns this path and
    opens a SQLite file in it; without it the first ``SqliteSessionService``
    open fails with "unable to open database file".
    """
    parent = Path(request.workspace) / ".claudio"
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f"adk-{request.session_id}.db"


class AdkEngine:
    """A :class:`claudio_core.engine.RunEngine` backed by Google ADK."""

    def __init__(self, config: AppConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._cancelled: set[str] = set()

    # -- construction helpers -------------------------------------------

    def _model(self, spec: ModelSpec) -> Any:
        from google.adk.integrations.openai import OpenAILlm

        return OpenAILlm(
            model=spec.model,
            base_url=spec.base_url,
            api_key=spec.api_key or "local",
        )

    def _build_agent(self, request: RunRequest, ctx: ToolContext) -> LlmAgent:
        tools: list[FunctionTool] = []
        for name in request.tools.enabled:
            if name not in TOOL_FUNCTIONS:
                continue
            needs = needs_approval_for(TOOL_META[name], request.tools.approval_mode)
            tools.append(make_adk_tool(name, ctx, require_confirmation=needs))
        return LlmAgent(
            name=request.metadata.get("agent_name", "claudio"),
            instruction=request.instructions,
            model=self._model(request.model),
            tools=tools,
        )

    def _app(self, app_name: str, agent: LlmAgent) -> App:
        return App(
            name=app_name,
            root_agent=agent,
            resumability_config=ResumabilityConfig(is_resumable=True),
        )

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.config.llm.price_input_per_mtok
            + output_tokens / 1_000_000 * self.config.llm.price_output_per_mtok
        )

    def _make_context(self, request: RunRequest) -> ToolContext:
        return ToolContext(
            config=self.config,
            session_id=request.session_id,
            workspace=request.workspace,
            approval_mode=request.tools.approval_mode,
            db=self.db,
            run_id=request.run_id,
        )

    # -- RunEngine API ---------------------------------------------------

    async def stream(self, request: RunRequest) -> AsyncIterator[dict[str, Any]]:
        ctx = self._make_context(request)
        agent = self._build_agent(request, ctx)
        app_name = f"claudio-{request.session_id}"
        app = self._app(app_name, agent)
        session_service = SqliteSessionService(db_path=str(adk_state_path(request)))
        # The ADK session is keyed by the Claudio session_id, not the run_id, so
        # a multi-turn conversation carries its own history across runs: each new
        # chat message appends to the same ADK session and the model sees the
        # prior turns. ``get_session`` returns None when absent, so this is a
        # plain get-or-create rather than a create that would clash on turn two.
        existing = await session_service.get_session(
            app_name=app_name, user_id=request.session_id, session_id=request.session_id
        )
        if existing is None:
            await session_service.create_session(
                app_name=app_name,
                user_id=request.session_id,
                session_id=request.session_id,
            )
        runner = Runner(app=app, session_service=session_service)
        new_message = types.Content(
            role="user", parts=[types.Part(text=request.user_input)]
        )
        async for evt in self._drive(runner, request, new_message):
            yield evt

    async def resume(
        self, request: RunRequest, state: Any
    ) -> AsyncIterator[dict[str, Any]]:
        pause = normalise_state(state)
        ctx = self._make_context(request)
        agent = self._build_agent(request, ctx)
        app_name = f"claudio-{request.session_id}"
        app = self._app(app_name, agent)
        session_service = SqliteSessionService(db_path=str(adk_state_path(request)))
        runner = Runner(app=app, session_service=session_service)

        if pause.approved:
            response: dict[str, Any] = {"confirmed": True}
        else:
            response = {
                "confirmed": False,
                "reason": pause.rejection_reason or "The user rejected this action.",
            }
        new_message = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=pause.confirmation_id,
                        name=CONFIRMATION_TOOL,
                        response=response,
                    )
                )
            ],
        )
        async for evt in self._drive(runner, request, new_message):
            yield evt

    async def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)

    # -- the shared drive loop -------------------------------------------

    async def _drive(
        self,
        runner: Runner,
        request: RunRequest,
        new_message: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        input_tokens = 0
        output_tokens = 0
        turns = 0
        final_text = ""

        agen = runner.run_async(
            user_id=request.session_id,
            session_id=request.session_id,
            new_message=new_message,
        )
        try:
            async for event in agen:
                if request.run_id in self._cancelled:
                    await agen.aclose()
                    yield _failed(request.run_id, "cancelled", "Run cancelled by user", False)
                    return

                tin, tout = usage_from_event(event)
                input_tokens += tin
                output_tokens += tout
                if tin:
                    # The engine is the only layer that knows how large the
                    # prompt actually was, so it publishes the gauge; the
                    # RunManager persists it.
                    yield {
                        "event": EventName.CONTEXT_USAGE.value,
                        "data": context_usage_payload(tin, self.config, "model"),
                    }

                pause = find_confirmation(event)
                if pause is not None:
                    await agen.aclose()
                    yield {
                        "event": EventName.APPROVAL_REQUIRED.value,
                        "data": {
                            "approval_id": pause.confirmation_id,
                            "tool": pause.tool_name,
                            "arguments": _json_arg(pause.arguments),
                            "reason": pause.hint
                            or f"{pause.tool_name} requires approval",
                        },
                    }
                    return

                for evt in translate_event(event):
                    if evt["event"] == EventName.TOKEN.value:
                        final_text += evt["data"]["text"]
                    yield evt

                if getattr(event, "author", "") != "user":
                    turns += 1
                    if request.max_turns and turns >= request.max_turns:
                        await agen.aclose()
                        yield _failed(
                            request.run_id,
                            "max_turns_exceeded",
                            f"Run stopped after {turns} turns (max_turns reached)",
                            False,
                        )
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as run_failed
            logger.exception("ADK run failed")
            yield _failed(
                request.run_id,
                "engine_error",
                str(exc) or exc.__class__.__name__,
                True,
            )
            return

        yield {
            "event": EventName.RUN_COMPLETED.value,
            "data": {
                "run_id": request.run_id,
                "session_id": request.session_id,
                "final_output": final_text,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": self._cost(input_tokens, output_tokens),
                },
            },
        }


def _failed(run_id: str, code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {
        "event": EventName.RUN_FAILED.value,
        "data": {"run_id": run_id, "error": {"code": code, "message": message, "retryable": retryable}},
    }


def create_engine(
    config: AppConfig,
    db: Database,
    kind: str | None = None,
) -> AdkEngine:
    """Build the engine for this deployment.

    ``kind`` selects the implementation and defaults to the ``CLAUDIO_ENGINE``
    environment variable, then to ``"adk"``. The parameter exists so the
    flag-switch described in the refactor plan (run the same flow through two
    engines and compare) is a constructor argument rather than a code change.
    """
    selected = (kind or os.environ.get("CLAUDIO_ENGINE") or "adk").strip().lower()
    if selected in ("adk", "google_adk", "google-adk"):
        return AdkEngine(config=config, db=db)
    raise EngineError(
        "unknown_engine",
        f"Unknown engine {selected!r}; this build provides 'adk'.",
        retryable=False,
    )


__all__ = [
    "AdkEngine",
    "AdkPauseState",
    "create_engine",
    "normalise_state",
    "make_adk_tool",
    "translate_event",
    "find_confirmation",
    "usage_from_event",
    "adk_state_path",
    "CONFIRMATION_TOOL",
]

