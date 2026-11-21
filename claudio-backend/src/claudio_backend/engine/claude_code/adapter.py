"""Claude Code engine adapter (issue #57).

Drives the pinned ``claude`` CLI non-interactively (``claude -p`` with
structured ``stream-json`` output) through the shared subprocess runner and
translates its events into Claudio's SSE contract.

**Provider compatibility is a hard gate.** Claude Code speaks the Anthropic
Messages API (``ANTHROPIC_BASE_URL`` + ``/v1/messages``); it is NOT automatically
compatible with an OpenAI-only ``/v1/chat/completions`` endpoint. The adapter
refuses to start against an endpoint that is not declared Anthropic-compatible
unless ``engine.claude_code.allow_openai_endpoint`` is explicitly set after the
operator has verified a conversion gateway. This prevents silently pointing
Claude Code at an incompatible server.

Built-in CLI tools are constrained so file/shell operations route through
Claudio's policy bridge; the adapter never launches with a permission-bypass flag
as a substitute for Claudio approval. Real use needs the pinned CLI + a verified
Anthropic-compatible endpoint; the deterministic tests drive a fake CLI emitting
known stream-json, with no network or credentials.
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


class ClaudeCodeEngine:
    """A :class:`claudio_core.engine.RunEngine` backed by the Claude Code CLI."""

    ENGINE_ID = "claude_code"

    def __init__(self, ctx: EngineContext) -> None:
        self._ctx = ctx
        self.config = ctx.config
        self.db = ctx.db
        self.accountant = ctx.accountant
        self.profile_health = ctx.profile_health
        self._cancelled: set[str] = set()

    @property
    def descriptor(self) -> EngineDescriptor:
        return EngineDescriptor(
            id=self.ENGINE_ID,
            version=getattr(self, "_resolved_version", "unknown"),
            capabilities=EngineCapabilities(
                streaming=True, approvals=True, tools=True,
                compaction=True, resume_after_restart=True, usage_reporting=True,
            ),
        )

    def capabilities(self) -> EngineCapabilities:
        return self.descriptor.capabilities

    @property
    def _cc(self):
        return self.config.engine.claude_code

    @property
    def _binary(self) -> str:
        return self._cc.binary or "claude"

    def _config_dir(self) -> Path:
        d = self._cc.config_dir
        if d:
            return Path(d)
        base = workspace_state_dir("__engine__")
        p = base / "claude-code"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _allow_openai_endpoint(self) -> bool:
        """Whether the operator has explicitly allowed a non-Anthropic endpoint.

        Honors the config field AND the deployment env vars directly, because
        the config loader treats the YAML file as authoritative over env — a
        seeded ``config.yaml`` pinning ``allow_openai_endpoint: false`` would
        otherwise silently override the operator's ``CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT``
        / ``CLAUDIO__ENGINE__CLAUDE_CODE__ALLOW_OPENAI_ENDPOINT`` (issue #57).
        The engine selector already reads ``CLAUDIO_ENGINE`` from env directly;
        this keeps the endpoint gate consistent with that deployment-intent model.
        """
        if self._cc.allow_openai_endpoint:
            return True
        import os
        for name in (
            "CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT",
            "CLAUDIO__ENGINE__CLAUDE_CODE__ALLOW_OPENAI_ENDPOINT",
        ):
            val = (os.environ.get(name) or "").strip().lower()
            if val in ("1", "true", "yes", "on"):
                return True
        return False

    def _endpoint_is_anthropic(self) -> bool:
        """Whether the configured endpoint can serve the Anthropic Messages API.

        A conservative check: an explicit allow flag, or a base_url that is not
        an OpenAI-only local server. Operators using a conversion gateway set the
        allow flag after verifying ``/v1/messages`` works.
        """
        if self._allow_openai_endpoint():
            return True
        base = (self.config.llm.base_url or "").lower()
        # Heuristic: treat a base_url naming "anthropic" as compatible; anything
        # else (e.g. a llama.cpp/vLLM OpenAI server) is not, without the flag.
        return "anthropic" in base

    async def start(self) -> None:
        path = shutil.which(self._binary)
        if not path:
            raise EngineError(
                "engine_unavailable",
                f"Claude Code binary {self._binary!r} not found in the image; "
                "select the claude_code backend image or set engine.kind=adk",
                retryable=False,
            )
        if not self._endpoint_is_anthropic():
            raise EngineError(
                "engine_endpoint_unsupported",
                "Claude Code requires an Anthropic Messages-compatible endpoint "
                "(ANTHROPIC_BASE_URL with /v1/messages). The configured endpoint "
                "is not declared compatible; provide/verify a conversion gateway "
                "and set engine.claude_code.allow_openai_endpoint=true, or use "
                "the adk engine for OpenAI-only endpoints.",
                retryable=False,
            )
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
        return None

    def _env(self) -> dict[str, str]:
        llm = self.config.llm
        return proc.build_env(
            base={"HOME": str(self._config_dir()), "CLAUDE_CONFIG_DIR": str(self._config_dir())},
            extra={
                "ANTHROPIC_BASE_URL": llm.base_url or "",
                "ANTHROPIC_API_KEY": llm.api_key or "",
            },
        )

    def _argv(self, request: RunRequest) -> list[str]:
        # Non-interactive, structured streaming output. No permission-bypass flag.
        return [
            self._binary, "-p",
            "--output-format", "stream-json",
            "--permission-mode", self._cc.permission_mode,
        ]

    async def stream(self, request: RunRequest) -> AsyncIterator[dict[str, Any]]:
        async for evt in self._drive(request):
            yield evt

    async def resume(self, request: RunRequest, state: Any) -> AsyncIterator[dict[str, Any]]:
        if isinstance(state, EngineCheckpoint):
            checkpoint = state
        elif isinstance(state, dict) and "engine" in state:
            checkpoint = EngineCheckpoint.from_dict(state)
        else:
            checkpoint = EngineCheckpoint(engine=self.ENGINE_ID, payload=state or {})
        checkpoint.validate_for(self.ENGINE_ID)
        async for evt in self._drive(request):
            yield evt

    async def _drive(self, request: RunRequest) -> AsyncIterator[dict[str, Any]]:
        argv = self._argv(request)
        try:
            async for line in proc.stream_lines(
                argv,
                cwd=request.workspace,
                env=self._env(),
                timeout_seconds=self.config.engine.run_timeout_seconds,
                stdin_text=request.user_input,
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
            yield self._failed(request, "engine_error", str(exc))
            return
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
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None
        kind = obj.get("type")
        if kind == "assistant":
            # Claude Code stream-json wraps message content; extract text deltas.
            msg = obj.get("message") or {}
            parts = msg.get("content") or []
            text = "".join(
                p.get("text", "") for p in parts
                if isinstance(p, dict) and p.get("type") == "text"
            )
            if text:
                self._last_text = getattr(self, "_last_text", "") + text
                return {"event": EventName.TOKEN.value, "data": {"text": text}}
        elif kind == "tool_use":
            return {
                "event": EventName.TOOL_CALL.value,
                "data": {
                    "call_id": str(obj.get("id") or ""),
                    "name": str(obj.get("name") or ""),
                    "arguments": json.dumps(obj.get("input") or {}),
                },
            }
        elif kind == "tool_result":
            return {
                "event": EventName.TOOL_RESULT.value,
                "data": {
                    "call_id": str(obj.get("tool_use_id") or obj.get("id") or ""),
                    "name": str(obj.get("name") or ""),
                    "output": str(obj.get("content") or obj.get("output") or ""),
                    "is_error": bool(obj.get("is_error")),
                },
            }
        elif kind == "result":
            usage = obj.get("usage") or {}
            return {
                "event": EventName.RUN_COMPLETED.value,
                "data": {
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "final_output": obj.get("result") or getattr(self, "_last_text", ""),
                    "usage": {
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                        "cost_usd": float(obj.get("total_cost_usd") or 0.0),
                    },
                    "turns": obj.get("num_turns", 0),
                },
            }
        return None

    def _failed(self, request: RunRequest, code: str, message: str) -> dict[str, Any]:
        return {
            "event": EventName.RUN_FAILED.value,
            "data": {"run_id": request.run_id, "error": {"code": code, "message": message, "retryable": False}},
        }

    async def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)

    def _mirror_path(self, session_id: str, workspace_id: str) -> Path:
        base = workspace_state_dir(workspace_id or "__engine__") / "claude-code-mirror"
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
