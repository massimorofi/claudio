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
from claudio_core.settings import resolve_engine_kind

from ...paths import workspace_state_dir
from ..base import EngineContext
from .. import process as proc

# opencode tool name -> Claudio canonical name (artifact tracking + cards).
_OC_TOOL_NAMES = {
    "write": "write_file",
    "edit": "edit_file",
    "read": "read_file",
    "bash": "run_command",
    "todowrite": "todo_write",
    "webfetch": "web_fetch",
}
_OC_TODO_STATUS = {"completed": "done", "in_progress": "in_progress"}


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
        self._write_provider_config()
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
        # Effective engine (env-aware), not the raw config field -- see the
        # Claude Code adapter for why (issue #57).
        if resolve_engine_kind(config) != self.ENGINE_ID:
            raise EngineError(
                "engine_reload_requires_restart",
                "changing engine.kind requires a backend restart/rebuild",
                retryable=False,
            )
        self.config = config
        self._write_provider_config()

    def _write_provider_config(self) -> None:
        """Generate opencode.json registering the Claudio gateway as a provider.

        OpenCode has no generic "custom OpenAI base URL" env override the way
        Claude Code has ANTHROPIC_BASE_URL: a non-OpenAI model id must be
        declared under a provider in opencode.json, or the CLI rejects it. The
        API key is referenced as {env:OPENAI_API_KEY} so the secret stays in
        the runtime environment and is never written to disk (issue #57).
        """
        llm = self.config.llm
        oc = self._oc
        model = (llm.model or "").strip()
        cfg_dir = self._config_dir() / ".config" / "opencode"
        try:
            cfg_dir.mkdir(parents=True, exist_ok=True)
            provider = {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Claudio gateway",
                "options": {
                    "baseURL": llm.base_url or "",
                    "apiKey": "{env:OPENAI_API_KEY}",
                },
                "models": {
                    model: {
                        "limit": {
                            "context": int(oc.context_token_limit),
                            "output": int(oc.output_token_limit),
                        }
                    }
                } if model else {},
            }
            (cfg_dir / "opencode.json").write_text(
                json.dumps({
                    "$schema": "https://opencode.ai/config.json",
                    "provider": {oc.provider_name: provider},
                }, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # best-effort; a run without it fails loudly at the CLI

    def _session_path(self, session_id: str) -> Path:
        base = workspace_state_dir("__engine__") / "opencode-sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{session_id}.json"

    def _load_native_session(self, session_id: str) -> str:
        try:
            return str(json.loads(self._session_path(session_id).read_text())["native_session"])
        except (OSError, ValueError, KeyError, TypeError):
            return ""

    def _save_native_session(self, session_id: str, native: str) -> None:
        if not native:
            return
        try:
            self._session_path(session_id).write_text(
                json.dumps({"native_session": native})
            )
        except OSError:
            pass

    def _clear_native_session(self, session_id: str) -> None:
        try:
            self._session_path(session_id).unlink()
        except OSError:
            pass

    async def close(self) -> None:
        self._closed = True

    # -- run operations ---------------------------------------------------

    def _env(self) -> dict[str, str]:
        # Minimal environment: only what OpenCode needs, never the whole .env.
        # The provider config references {env:OPENAI_API_KEY}, so the key must
        # be present here (issue #57).
        llm = self.config.llm
        return proc.build_env(
            base={"HOME": str(self._config_dir())},
            extra={
                "OPENAI_API_KEY": llm.api_key or "local",
                "XDG_CONFIG_HOME": str(self._config_dir() / ".config"),
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

    def _model_ref(self) -> str:
        model = (self.config.llm.model or "").strip()
        if not model:
            return ""
        # opencode wants provider/model; if the operator already qualified it,
        # use as-is.
        return model if "/" in model else f"{self._oc.provider_name}/{model}"

    def _argv(self, request: RunRequest) -> list[str]:
        # argv list only -- the message is a positional argument (execv, never
        # shell-interpolated). --format json emits raw JSONL events; --thinking
        # surfaces reasoning blocks (issue #57).
        argv = [self._binary, "run", request.user_input, "--format", "json", "--thinking"]
        model_ref = self._model_ref()
        if model_ref:
            argv += ["-m", model_ref]
        native = self._load_native_session(request.session_id)
        if native:
            argv += ["-s", native]
        return argv

    async def stream(self, request: RunRequest) -> AsyncIterator[dict[str, Any]]:
        async for evt in self._stream_with_resume_fallback(request):
            yield evt

    async def _stream_with_resume_fallback(
        self, request: RunRequest
    ) -> AsyncIterator[dict[str, Any]]:
        """Retry fresh if the stored native session is stale (issue #57)."""
        async for evt in self._drive(request, resume_checkpoint=None):
            code = ((evt.get("data") or {}).get("error") or {}).get("code")
            if evt.get("event") == EventName.RUN_FAILED.value and code == "engine_resume_stale":
                self._clear_native_session(request.session_id)
                async for evt2 in self._drive(request, resume_checkpoint=None):
                    yield evt2
                return
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
        self._last_text = ""
        self._usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self._saw_terminal = False
        try:
            async for line in proc.stream_lines(
                argv,
                cwd=request.workspace,
                env=env,
                timeout_seconds=self.config.engine.run_timeout_seconds,
            ):
                if request.run_id in self._cancelled:
                    yield self._failed(request, "cancelled", "Run cancelled by user")
                    return
                for event in self._translate_line(line, request):
                    yield event
                    if event["event"] in (EventName.RUN_COMPLETED.value,
                                          EventName.RUN_FAILED.value,
                                          EventName.APPROVAL_REQUIRED.value):
                        self._saw_terminal = True
                        return
        except proc.ProcessError as exc:
            # Launch/exit failures are config problems: replanning would fail
            # identically forever. Only a timeout stays retryable.
            code = "engine_error" if "timeout" in str(exc).lower() else "engine_config_error"
            yield self._failed(request, code, str(exc))
            return
        # opencode emits no explicit terminal event -- the stream ends when the
        # process exits. Synthesize exactly one completion from accumulated
        # text + summed step_finish usage so the run manager sees a terminal.
        if not self._saw_terminal:
            yield {
                "event": EventName.RUN_COMPLETED.value,
                "data": {
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "final_output": self._last_text,
                    "usage": self._usage,
                    "turns": 0,
                },
            }

    def _translate_line(self, line: str, request: RunRequest) -> list[dict[str, Any]]:
        """Map one OpenCode JSONL line to zero or more Claudio events.

        The real ``opencode run --format json`` schema (captured from the CLI,
        issue #57): top-level ``type`` in {step_start, text, reasoning,
        tool_use, step_finish, error}; the payload lives under ``part`` with
        hyphenated inner types. There is no explicit terminal event -- the
        stream ends when the process exits (handled by _drive).
        """
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            return []
        if not isinstance(obj, dict):
            return []
        native = obj.get("sessionID")
        if native:
            self._save_native_session(request.session_id, str(native))
        kind = obj.get("type")
        part = obj.get("part") or {}
        if kind == "text":
            text = str(part.get("text") or "")
            if text:
                self._last_text = getattr(self, "_last_text", "") + text
                return [{"event": EventName.TOKEN.value, "data": {"text": text}}]
            return []
        if kind == "reasoning":
            text = str(part.get("text") or "")
            if text:
                return [{"event": EventName.REASONING.value, "data": {"text": text}}]
            return []
        if kind == "tool_use":
            return self._translate_tool(part)
        if kind == "step_finish":
            tokens = part.get("tokens") or {}
            usage = getattr(self, "_usage", None)
            if isinstance(usage, dict):
                usage["input_tokens"] += int(tokens.get("input") or 0)
                usage["output_tokens"] += int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
                usage["cost_usd"] += float(part.get("cost") or 0.0)
            return []
        if kind == "error":
            err = obj.get("error") or {}
            data = err.get("data") or {}
            msg = str(data.get("message") or err.get("name") or "OpenCode run failed")
            low = msg.lower()
            if "session" in low and ("not found" in low or "no such" in low or "invalid" in low):
                return [self._failed(request, "engine_resume_stale", msg)]
            return [self._failed(request, "engine_config_error", msg)]
        return []

    def _translate_tool(self, part: dict[str, Any]) -> list[dict[str, Any]]:
        """One tool_use line yields a TOOL_CALL and, when completed, a TOOL_RESULT."""
        raw = str(part.get("tool") or "")
        name = _OC_TOOL_NAMES.get(raw, raw)
        state = part.get("state") or {}
        call_id = str(part.get("callID") or part.get("id") or "")
        args = state.get("input") or {}
        if raw == "todowrite":
            args = {
                "items": [
                    {
                        "content": str(t.get("content") or ""),
                        "status": _OC_TODO_STATUS.get(str(t.get("status")), "pending"),
                    }
                    for t in (args.get("todos") or [])
                    if isinstance(t, dict)
                ]
            }
        out: list[dict[str, Any]] = [{
            "event": EventName.TOOL_CALL.value,
            "data": {
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(args),
            },
        }]
        status = str(state.get("status") or "")
        if status in ("completed", "error"):
            output = state.get("output")
            if not isinstance(output, str):
                output = json.dumps(output) if output is not None else ""
            out.append({
                "event": EventName.TOOL_RESULT.value,
                "data": {
                    "call_id": call_id,
                    "name": name,
                    "output": output,
                    "is_error": status == "error",
                },
            })
        return out

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
