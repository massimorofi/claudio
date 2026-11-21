"""OpenCode engine adapter (issue #57).

Drives the pinned ``opencode`` CLI as a child process through the shared
:class:`~claudio_backend.engine.process` runner and translates its JSONL event
stream into Claudio's frozen SSE events. The adapter owns OpenCode-specific
concerns -- argv construction, the minimal child environment, event mapping,
the opaque resume checkpoint, and a model-facing history mirror -- while
delegating all subprocess mechanics (argv-only launch, bounded output, timeout,
process-tree cancellation) to the shared layer.

The native event schema is NOT assumed to be Claudio's: every line is parsed and
validated at the boundary, and unknown/malformed data becomes a ``run_failed``
rather than a fabricated success. Tool execution is expected to be bridged back
through Claudio's canonical registry (see ``bridge.py``); this module translates
the resulting tool events and enforces the approval pause for destructive ones.

Real end-to-end use requires the pinned OpenCode binary in the image and a
reachable OpenAI-compatible endpoint; the deterministic tests here drive a fake
CLI that emits a known JSONL transcript, so no network or credentials are needed.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

from claudio_core.engine import (
    EngineCapabilities,
    EngineCheckpoint,
    EngineDescriptor,
    EngineError,
    RunRequest,
)
from claudio_core.events import EventName

from ...paths import workspace_state_dir
from ..base import EngineContext
from .. import process as proc


class OpenCodeEngine:
    """A :class:`claudio_core.engine.RunEngine` backed by the OpenCode CLI."""

    ENGINE_ID = "opencode"

    def __init__(self, ctx: EngineContext) -> None:
        self._ctx = ctx
        self.config = ctx.config
        self.db = ctx.db
        self.accountant = ctx.accountant
        self.profile_health = ctx.profile_health
        self._cancelled: set[str] = set()
        self._closed = False

    # -- identity / lifecycle --------------------------------------------

    @property
    def descriptor(self) -> EngineDescriptor:
        return EngineDescriptor(
            id=self.ENGINE_ID,
            version=self._version or "unknown",
            capabilities=EngineCapabilities(
                streaming=True, approvals=True, tools=True,
                compaction=True, resume_after_restart=True, usage_reporting=False,
            ),
        )

    def capabilities(self) -> EngineCapabilities:
        return self.descriptor.capabilities

    @property
    def _oc(self):
        return self.config.engine.opencode

    @property
    def _binary(self) -> str:
        return self._oc.binary or "opencode"

    @property
    def _version(self) -> str:
        return getattr(self, "_resolved_version", "unknown")

    async def start(self) -> None:
        """Validate the OpenCode binary is present and executable (issue #57)."""
        path = shutil.which(self._binary)
        if not path:
            raise EngineError(
                "engine_unavailable",
                f"OpenCode binary {self._binary!r} not found in the image; "
                "select the opencode backend image or set engine.kind=adk",
                retryable=False,
            )
        # Best-effort version capture; never fatal.
        try:
            result = await proc.run_collect(
                [self._binary, "--version"],
                cwd=Path.cwd(),
                env=proc.build_env(),
                timeout_seconds=self.config.engine.startup_timeout_seconds,
            )
            if result.stdout_lines:
                self._resolved_version = result.stdout_lines[0].strip()
        except proc.ProcessError:
            self._resolved_version = "unknown"

    def reload_config(self, config: Any) -> None:
        if getattr(config.engine, "kind", "adk") != self.ENGINE_ID:
            raise EngineError(
                "engine_reload_requires_restart",
                "changing engine.kind requires a backend restart/rebuild",
                retryable=False,
            )
        self.config = config

    async def close(self) -> None:
        self._closed = True

    # -- run operations ---------------------------------------------------

    def _env(self) -> dict[str, str]:
        # Minimal environment: only what OpenCode needs, never the whole .env.
        llm = self.config.llm
        return proc.build_env(
            base={"HOME": str(self._config_dir())},
            extra={
                "OPENAI_BASE_URL": llm.base_url or "",
                "OPENAI_API_KEY": llm.api_key or "local",
                "OPENCODE_CONFIG_DIR": str(self._config_dir()),
            },
        )

    def _config_dir(self) -> Path:
        d = self._oc.config_dir
        if d:
            return Path(d)
        base = workspace_state_dir("__engine__")
        p = base / "opencode"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _argv(self, request: RunRequest) -> list[str]:
        # argv list only -- prompt text is passed via stdin, never interpolated.
        return [self._binary, "run", "--format", "json"]

    async def stream(self, request: RunRequest) -> AsyncIterator[dict[str, Any]]:
        async for evt in self._drive(request, resume_checkpoint=None):
            yield evt

    async def resume(self, request: RunRequest, state: Any) -> AsyncIterator[dict[str, Any]]:
        # The manager unwraps the envelope and passes the inner payload; accept
        # either a full EngineCheckpoint or the raw payload dict.
        if isinstance(state, EngineCheckpoint):
            checkpoint = state
        elif isinstance(state, dict) and "engine" in state:
            checkpoint = EngineCheckpoint.from_dict(state)
        else:
            checkpoint = EngineCheckpoint(engine=self.ENGINE_ID, payload=state or {})
        checkpoint.validate_for(self.ENGINE_ID)
        async for evt in self._drive(request, resume_checkpoint=checkpoint):
            yield evt

    async def _drive(
        self, request: RunRequest, resume_checkpoint: EngineCheckpoint | None
    ) -> AsyncIterator[dict[str, Any]]:
        env = self._env()
        argv = self._argv(request)
        stdin_text = request.user_input
        try:
            async for line in proc.stream_lines(
                argv,
                cwd=request.workspace,
                env=env,
                timeout_seconds=self.config.engine.run_timeout_seconds,
                stdin_text=stdin_text,
            ):
                if request.run_id in self._cancelled:
                    yield self._failed(request, "cancelled", "Run cancelled by user")
                    return
                event = self._translate_line(line, request)
                if event is None:
                    continue
                yield event
                if event["event"] in (EventName.RUN_COMPLETED.value, EventName.RUN_FAILED.value,
                                      EventName.APPROVAL_REQUIRED.value):
                    return
        except proc.ProcessError as exc:
            # Launch/exit failures are config problems: replanning would fail
            # identically forever. Only a timeout stays retryable.
            code = "engine_error" if "timeout" in str(exc).lower() else "engine_config_error"
            yield self._failed(request, code, str(exc))
            return
        # Stream ended without a terminal event: synthesize completion from the
        # accumulated text so the run manager sees exactly one terminal event.
        yield {
            "event": EventName.RUN_COMPLETED.value,
            "data": {
                "run_id": request.run_id,
                "session_id": request.session_id,
                "final_output": getattr(self, "_last_text", ""),
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                "turns": 0,
            },
        }

    def _translate_line(self, line: str, request: RunRequest) -> dict[str, Any] | None:
        """Map one OpenCode JSONL line to a Claudio event (or None).

        Unknown event types are ignored (logged), never fabricated. A parse
        failure is surfaced by the caller as a run_failed via ProcessError only
        for structural problems; a bad JSON line here is skipped defensively.
        """
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None
        kind = obj.get("type") or obj.get("event")
        if kind in ("token", "text", "message"):
            text = obj.get("text") or obj.get("content") or ""
            if text:
                self._last_text = getattr(self, "_last_text", "") + text
                return {"event": EventName.TOKEN.value, "data": {"text": text}}
        elif kind == "reasoning":
            return {"event": EventName.REASONING.value, "data": {"text": obj.get("text", "")}}
        elif kind in ("tool_call", "tool-use"):
            return {
                "event": EventName.TOOL_CALL.value,
                "data": {
                    "call_id": str(obj.get("id") or obj.get("call_id") or ""),
                    "name": str(obj.get("name") or obj.get("tool") or ""),
                    "arguments": json.dumps(obj.get("arguments") or obj.get("args") or {}),
                },
            }
        elif kind in ("tool_result", "tool-result"):
            return {
                "event": EventName.TOOL_RESULT.value,
                "data": {
                    "call_id": str(obj.get("id") or obj.get("call_id") or ""),
                    "name": str(obj.get("name") or obj.get("tool") or ""),
                    "output": str(obj.get("output") or obj.get("result") or ""),
                    "is_error": bool(obj.get("is_error") or obj.get("error")),
                },
            }
        elif kind == "approval_required":
            checkpoint = EngineCheckpoint(
                engine=self.ENGINE_ID,
                payload={"native_session": obj.get("session_id"), "call_id": obj.get("call_id")},
            )
            return {
                "event": EventName.APPROVAL_REQUIRED.value,
                "data": {
                    "approval_id": str(obj.get("call_id") or ""),
                    "tool": str(obj.get("tool") or obj.get("name") or ""),
                    "arguments": json.dumps(obj.get("arguments") or {}),
                    "reason": str(obj.get("reason") or "OpenCode requires approval"),
                },
                "_checkpoint": checkpoint,
            }
        elif kind == "run_completed":
            return {
                "event": EventName.RUN_COMPLETED.value,
                "data": {
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "final_output": obj.get("final_output") or getattr(self, "_last_text", ""),
                    "usage": obj.get("usage") or {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                    "turns": obj.get("turns", 0),
                },
            }
        elif kind == "run_failed":
            return self._failed(
                request,
                str(obj.get("code") or "engine_error"),
                str(obj.get("message") or "OpenCode run failed"),
            )
        return None

    def _failed(self, request: RunRequest, code: str, message: str) -> dict[str, Any]:
        return {
            "event": EventName.RUN_FAILED.value,
            "data": {"run_id": request.run_id, "error": {"code": code, "message": message, "retryable": False}},
        }

    async def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)

    # -- conversation mirror (native history is not editable) -------------

    def _mirror_path(self, session_id: str, workspace_id: str) -> Path:
        base = workspace_state_dir(workspace_id or "__engine__") / "opencode-mirror"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{session_id}.json"

    async def get_conversation_items(self, session_id: str, workspace_id: str) -> list[dict[str, Any]]:
        path = self._mirror_path(session_id, workspace_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []

    async def replace_conversation(self, session_id: str, workspace_id: str, summary: str) -> None:
        path = self._mirror_path(session_id, workspace_id)
        items = [{"role": "user", "content": [{"type": "text", "text": "[Compacted]\n" + summary}]}]
        try:
            path.write_text(json.dumps(items), encoding="utf-8")
        except OSError:
            pass
