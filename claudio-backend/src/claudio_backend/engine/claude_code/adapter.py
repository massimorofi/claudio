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
import logging
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger("claudio.engine.claude_code")

# CLI todo status -> Claudio PlanItem status (frontend: pending|in_progress|done).
_TODO_STATUS = {"completed": "done", "in_progress": "in_progress"}


def _plan_item(todo: dict[str, Any]) -> dict[str, str]:
    return {
        "content": str(todo.get("content") or todo.get("activeForm") or ""),
        "status": _TODO_STATUS.get(str(todo.get("status")), "pending"),
    }


def _flatten_tool_content(content: Any) -> str:
    """tool_result content is a string or a list of {type:text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(c.get("text", "")) for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return str(content or "")

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

    def _anthropic_base_url(self) -> str:
        """Translate Claudio's OpenAI-convention base_url to the CLI's form.

        ``llm.base_url`` ends with ``/v1`` because that is what the OpenAI
        protocol (adk/opencode engines) needs. Claude Code appends
        ``/v1/messages`` itself, so it must receive the server ROOT: passing
        the ``/v1`` form makes it request ``/v1/v1/messages`` and fail with a
        misleading "issue with the selected model" error (issue #57).
        """
        base = (self.config.llm.base_url or "").strip().rstrip("/")
        if base.lower().endswith("/v1"):
            base = base[:-3]
        return (base + "/") if base else ""

    def _env(self) -> dict[str, str]:
        llm = self.config.llm
        extra = {
            "ANTHROPIC_BASE_URL": self._anthropic_base_url(),
            "ANTHROPIC_API_KEY": llm.api_key or "",
        }
        model = (llm.model or "").strip()
        if model:
            # Pin the model for the main loop AND for the CLI's background
            # helper calls (topic detection, summaries), which otherwise
            # default to a Haiku id a custom gateway may not serve.
            extra["ANTHROPIC_MODEL"] = model
            extra["ANTHROPIC_SMALL_FAST_MODEL"] = model
        return proc.build_env(
            base={"HOME": str(self._config_dir()), "CLAUDE_CONFIG_DIR": str(self._config_dir())},
            extra=extra,
        )

    def _cli_session_path(self, session_id: str) -> Path:
        base = workspace_state_dir("__engine__") / "claude-code-sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{session_id}.json"

    def _load_cli_session(self, session_id: str) -> str:
        try:
            return str(json.loads(self._cli_session_path(session_id).read_text())["cli_session_id"])
        except (OSError, ValueError, KeyError, TypeError):
            return ""

    def _save_cli_session(self, session_id: str, cli_session_id: str) -> None:
        if not cli_session_id:
            return
        try:
            self._cli_session_path(session_id).write_text(
                json.dumps({"cli_session_id": cli_session_id})
            )
        except OSError:
            pass

    def _clear_cli_session(self, session_id: str) -> None:
        try:
            self._cli_session_path(session_id).unlink()
        except OSError:
            pass

    def _argv(self, request: RunRequest) -> list[str]:
        # Non-interactive, structured streaming output. No permission-bypass
        # flag. NOTE: the CLI refuses to start with
        # "--output-format=stream-json requires --verbose" when printing, so
        # --verbose is mandatory here (without it the run exits 1 with an empty
        # stdout and the chat shows "no output").
        argv = [
            self._binary, "-p",
            "--output-format", "stream-json",
            "--verbose",
        ]
        model = (self.config.llm.model or "").strip()
        if model:
            # Without --model the CLI uses its built-in default (e.g.
            # claude-opus-5-5), which a custom gateway does not serve.
            argv += ["--model", model]
        argv += ["--permission-mode", self._cc.permission_mode]
        # Conversation continuity: the CLI keeps its own transcript per session
        # under CLAUDE_CONFIG_DIR; --resume replays it so turn 2 sees turn 1.
        # A stale id (config dir wiped by a rebuild) is handled by the
        # fresh-run fallback in _stream_with_resume_fallback.
        cli_session = self._load_cli_session(request.session_id)
        if cli_session:
            argv += ["--resume", cli_session]
        return argv

    async def stream(self, request: RunRequest) -> AsyncIterator[dict[str, Any]]:
        async for evt in self._stream_with_resume_fallback(request):
            yield evt

    async def resume(self, request: RunRequest, state: Any) -> AsyncIterator[dict[str, Any]]:
        if isinstance(state, EngineCheckpoint):
            checkpoint = state
        elif isinstance(state, dict) and "engine" in state:
            checkpoint = EngineCheckpoint.from_dict(state)
        else:
            checkpoint = EngineCheckpoint(engine=self.ENGINE_ID, payload=state or {})
        checkpoint.validate_for(self.ENGINE_ID)
        async for evt in self._stream_with_resume_fallback(request):
            yield evt

    async def _stream_with_resume_fallback(
        self, request: RunRequest
    ) -> AsyncIterator[dict[str, Any]]:
        """Run once; if the stored CLI session is stale, retry fresh.

        ``--resume`` fails with "No conversation found" when the CLI config
        dir was wiped (image rebuild). Retrying without --resume inside the
        adapter keeps the user's original message intact -- the run manager's
        generic replan would resend a replan prompt instead.
        """
        async for evt in self._drive(request):
            code = ((evt.get("data") or {}).get("error") or {}).get("code")
            if evt.get("event") == EventName.RUN_FAILED.value and code == "engine_resume_stale":
                self._clear_cli_session(request.session_id)
                async for evt2 in self._drive(request):
                    yield evt2
                return
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
                for event in self._translate_line(line, request):
                    yield event
                    if event["event"] in (EventName.RUN_COMPLETED.value,
                                          EventName.RUN_FAILED.value,
                                          EventName.APPROVAL_REQUIRED.value):
                        return
        except proc.ProcessError as exc:
            # A launch/exit failure (bad argv, missing binary) is a config
            # problem: replanning would fail identically forever. Only a
            # timeout stays retryable.
            code = "engine_error" if "timeout" in str(exc).lower() else "engine_config_error"
            yield self._failed(request, code, str(exc))
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

    def _translate_line(self, line: str, request: RunRequest) -> list[dict[str, Any]]:
        """Translate one stream-json line into zero or more Claudio events.

        The CLI nests everything (text, thinking, tool_use) as content blocks
        inside "assistant" messages and returns tool results inside "user"
        messages -- a top-level ``tool_use`` line never occurs in practice, so
        the blocks must be unwrapped here or the UI shows no tool cards, no
        reasoning, and no plan.
        """
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            return []
        if not isinstance(obj, dict):
            return []
        kind = obj.get("type")
        if kind in ("system", "result"):
            # Remember the CLI's own session id so the next turn can --resume.
            sid = obj.get("session_id")
            if sid:
                self._save_cli_session(request.session_id, str(sid))
        if kind == "assistant":
            return self._translate_blocks(
                (obj.get("message") or {}).get("content") or []
            )
        if kind == "user":
            out = []
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append({
                        "event": EventName.TOOL_RESULT.value,
                        "data": {
                            "call_id": str(block.get("tool_use_id") or ""),
                            "name": str(block.get("name") or ""),
                            "output": _flatten_tool_content(block.get("content")),
                            "is_error": bool(block.get("is_error")),
                        },
                    })
            return out
        if kind == "tool_use":  # legacy top-level form
            return [{
                "event": EventName.TOOL_CALL.value,
                "data": {
                    "call_id": str(obj.get("id") or ""),
                    "name": str(obj.get("name") or ""),
                    "arguments": json.dumps(obj.get("input") or {}),
                },
            }]
        if kind == "tool_result":  # legacy top-level form
            return [{
                "event": EventName.TOOL_RESULT.value,
                "data": {
                    "call_id": str(obj.get("tool_use_id") or obj.get("id") or ""),
                    "name": str(obj.get("name") or ""),
                    "output": _flatten_tool_content(obj.get("content")),
                    "is_error": bool(obj.get("is_error")),
                },
            }]
        if kind == "result":
            usage = obj.get("usage") or {}
            text = obj.get("result") or getattr(self, "_last_text", "")
            denials = obj.get("permission_denials") or []
            if denials:
                logger.warning(
                    "run %s: %d tool permission(s) denied by the CLI",
                    request.run_id, len(denials),
                )
            if obj.get("is_error"):
                low = str(text).lower()
                if "no conversation found" in low or ("resume" in low and "session" in low):
                    # Stale --resume target (config dir wiped): let the
                    # adapter's fresh-run fallback handle it.
                    return [self._failed(request, "engine_resume_stale", str(text))]
                # The CLI exits 0 even when the run failed (unknown model,
                # auth problem, bad endpoint path ...): the failure lives in
                # this line. Terminal: replanning cannot fix a rejected
                # model/endpoint.
                return [self._failed(
                    request, "engine_config_error",
                    str(text or "Claude Code reported an error"),
                )]
            return [{
                "event": EventName.RUN_COMPLETED.value,
                "data": {
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "final_output": text,
                    "usage": {
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                        "cost_usd": float(obj.get("total_cost_usd") or 0.0),
                    },
                    "turns": obj.get("num_turns", 0),
                },
            }]
        return []

    def _translate_blocks(self, parts: list[Any]) -> list[dict[str, Any]]:
        """Unwrap assistant content blocks into Claudio events, in order."""
        out: list[dict[str, Any]] = []
        for p in parts:
            if not isinstance(p, dict):
                continue
            btype = p.get("type")
            if btype == "text" and p.get("text"):
                text = str(p["text"])
                self._last_text = getattr(self, "_last_text", "") + text
                out.append({"event": EventName.TOKEN.value, "data": {"text": text}})
            elif btype == "thinking" and p.get("thinking"):
                # Persisted by the run manager into the session transcript, so
                # reasoning survives a view remount (tab switch).
                out.append({
                    "event": EventName.REASONING.value,
                    "data": {"text": str(p["thinking"])},
                })
            elif btype == "tool_use":
                name = str(p.get("name") or "")
                args = p.get("input") or {}
                if name == "TodoWrite":
                    # Map the CLI todo list onto Claudio's plan contract so the
                    # PLAN panel renders it (frontend expects todo_write +
                    # {"items": [{content, status}]}).
                    name = "todo_write"
                    args = {
                        "items": [
                            _plan_item(t) for t in (args.get("todos") or [])
                            if isinstance(t, dict)
                        ]
                    }
                out.append({
                    "event": EventName.TOOL_CALL.value,
                    "data": {
                        "call_id": str(p.get("id") or ""),
                        "name": name,
                        "arguments": json.dumps(args),
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
