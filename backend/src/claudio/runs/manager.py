"""Run manager: active-run registry, event ring buffers, cancellation, budgets.

Each chat command becomes a *run* owned by the backend (not the client connection).
A run drives ``Runner.run_streamed``, translates SDK events to our SSE protocol,
and buffers them so a disconnected client can reconnect and resume. Runs pause on
human approval and resume from persisted ``RunState``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import Runner

from ..db import Database, utcnow
from ..settings import AppConfig
from ..agent.builder import build_primary_agent, build_subagent, _make_model
from ..agent.context import ToolContext
from .events import translate_stream_event

logger = logging.getLogger(__name__)

RING_BUFFER_SIZE = 2000


@dataclass
class RunRecord:
    run_id: str
    session_id: str
    state: str = "running"  # running | awaiting_approval | completed | failed | cancelled
    events: deque = field(default_factory=lambda: deque(maxlen=RING_BUFFER_SIZE))
    seq: int = 0
    subscribers: set = field(default_factory=set)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    turns_used: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    final_output: str = ""
    error_code: str | None = None
    error_message: str | None = None
    resume_state_json: str | None = None


class RunManager:
    def __init__(
        self,
        config: AppConfig,
        db: Database,
        session_store,
        metrics,
        compactor,
    ) -> None:
        self.config = config
        self.db = db
        self.session_store = session_store
        self.metrics = metrics
        self.compactor = compactor
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    # ---- public API -----------------------------------------------------

    async def start_run(
        self,
        run_id: str,
        session_id: str,
        message: str,
        approval_mode: str | None = None,
    ) -> RunRecord:
        async with self._lock:
            record = RunRecord(run_id=run_id, session_id=session_id)
            self._runs[run_id] = record
        await self.db.create_run(run_id, session_id)
        record.task = asyncio.create_task(
            self._drive_new_run(record, message, approval_mode)
        )
        return record

    async def resume_run(
        self,
        run_id: str,
        session_id: str,
        state_json: str,
        approval_id: str,
        decision: str,
        rejection_message: str | None = None,
    ) -> RunRecord:
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                record = RunRecord(run_id=run_id, session_id=session_id)
                self._runs[run_id] = record
            record.state = "running"
            record.resume_state_json = state_json
        record.task = asyncio.create_task(
            self._drive_resumed_run(
                record, state_json, approval_id, decision, rejection_message
            )
        )
        return record

    async def cancel_run(self, run_id: str) -> bool:
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False
            record.cancel_event.set()
            if record.task and not record.task.done():
                record.task.cancel()
        record.state = "cancelled"
        await self.db.update_run(
            run_id,
            state="cancelled",
            finished_at=utcnow(),
            error_code="cancelled",
            error_message="Run cancelled by user",
        )
        self._publish(
            record,
            {
                "event": "run_failed",
                "data": {
                    "run_id": run_id,
                    "error": {
                        "code": "cancelled",
                        "message": "Run cancelled by user",
                        "retryable": False,
                    },
                },
            },
        )
        return True

    def get_record(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def get_active_run_for_session(self, session_id: str) -> RunRecord | None:
        """Return the most recent still-active run for a session, if any.

        Active means ``running`` or ``awaiting_approval``. Used by the UI to
        reattach to an in-flight run after a reload.
        """
        record: RunRecord | None = None
        for r in self._runs.values():
            if r.session_id == session_id and r.state in ("running", "awaiting_approval"):
                record = r  # last matching in insertion order == most recent
        return record

    def active_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.state == "running")

    async def subscribe(self, run_id: str, after_seq: int = 0):
        """Async generator yielding SSE events for a run from after_seq onward."""
        opened = await self.open_stream(run_id, after_seq)
        if opened is None:
            return
        queue, replay = opened
        try:
            for _seq, evt in replay:
                yield evt
            while True:
                evt = await queue.get()
                if evt is None:  # sentinel
                    break
                yield evt
        finally:
            await self.close_stream(run_id, queue)

    async def open_stream(self, run_id: str, after_seq: int = 0):
        """Register a live subscriber queue and return (queue, replay_events).

        The caller owns the read loop (and must call :meth:`close_stream`).
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return None
            queue: asyncio.Queue = asyncio.Queue()
            record.subscribers.add(queue)
            replay = [(seq, evt) for seq, evt in record.events if seq > after_seq]
            return queue, replay

    async def close_stream(self, run_id: str, queue) -> None:
        async with self._lock:
            record = self._runs.get(run_id)
            if record:
                record.subscribers.discard(queue)

    # ---- internals ------------------------------------------------------

    def _publish(self, record: RunRecord, sse: dict[str, Any]) -> None:
        record.seq += 1
        record.events.append((record.seq, sse))
        for q in list(record.subscribers):
            try:
                q.put_nowait(sse)
            except asyncio.QueueFull:
                pass

    def _build_context(self, record: RunRecord, approval_mode: str | None) -> ToolContext:
        mode = approval_mode or self.config.agent.approval_mode
        workspace = Path(self.config.agent.workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        session_row = None
        return ToolContext(
            config=self.config,
            session_id=record.session_id,
            workspace=workspace,
            approval_mode=mode,
            db=self.db,
            run_id=record.run_id,
            turns_since_todo=0,
            agent_factory=lambda aid: build_subagent(self.config, aid),
        )

    async def _drive_new_run(
        self, record: RunRecord, message: str, approval_mode: str | None
    ) -> None:
        try:
            agent = build_primary_agent(self.config)
            tctx = self._build_context(record, approval_mode)
            session = self.session_store.make_sdk_session(record.session_id)
            self.metrics.inc_run_started()
            self._publish(
                record,
                {
                    "event": "run_started",
                    "data": {"run_id": record.run_id, "session_id": record.session_id},
                },
            )
            await self._run_stream(record, agent, message, tctx, session)
        except asyncio.CancelledError:
            await self._finalize_cancelled(record)
        except Exception as e:  # noqa: BLE001
            await self._finalize_failed(record, e)

    async def _drive_resumed_run(
        self,
        record: RunRecord,
        state_json: str,
        approval_id: str,
        decision: str,
        rejection_message: str | None,
    ) -> None:
        try:
            agent = build_primary_agent(self.config)
            tctx = self._build_context(record, None)
            session = self.session_store.make_sdk_session(record.session_id)
            state = await self._load_state(agent, state_json)
            # Resolve the pending approval.
            interruptions = state.get_interruptions()
            for item in interruptions:
                if decision == "approve":
                    state.approve(item)
                else:
                    state.reject(
                        item,
                        rejection_message=rejection_message
                        or "The user denied this action.",
                    )
            await self._run_stream(record, agent, state, tctx, session)
        except asyncio.CancelledError:
            await self._finalize_cancelled(record)
        except Exception as e:  # noqa: BLE001
            await self._finalize_failed(record, e)

    async def _load_state(self, agent, state_json: str):
        from agents import RunState

        data = json.loads(state_json) if isinstance(state_json, str) else state_json
        return RunState.from_json(agent, data)

    async def _run_stream(self, record, agent, input_data, tctx, session) -> None:
        """Drive a streamed run to completion, interruption, or failure."""
        result = Runner.run_streamed(
            agent,
            input_data,
            context=tctx,
            max_turns=self.config.agent.max_turns,
            session=session,
        )
        async for event in result.stream_events():
            if record.cancel_event.is_set():
                await self._finalize_cancelled(record)
                return
            for sse in translate_stream_event(event):
                self._publish(record, sse)

        # After the stream: interruptions (approval) or completion.
        interruptions = getattr(result, "interruptions", None)
        if interruptions:
            await self._handle_interruption(record, result)
            return

        await self._finalize_completed(record, result)

    async def _handle_interruption(self, record: RunRecord, result) -> None:
        state = result.to_state()
        state_json = state.to_json()
        record.resume_state_json = state_json
        record.state = "awaiting_approval"
        # Persist resume state on the run row.
        await self.db.update_run(
            record.run_id, resume_state_json=state_json, state="awaiting_approval"
        )
        for item in result.interruptions:
            tool_name = getattr(item, "tool_name", None) or getattr(item, "name", "?")
            call_id = getattr(item, "call_id", "")
            arguments = _extract_arguments(item)
            approval_id = await self.db.create_approval(
                run_id=record.run_id,
                session_id=record.session_id,
                tool_name=tool_name,
                call_id=call_id,
                arguments=arguments,
                reason=f"Approval required to run {tool_name}",
            )
            self._publish(
                record,
                {
                    "event": "approval_required",
                    "data": {
                        "approval_id": approval_id,
                        "tool": tool_name,
                        "arguments": arguments,
                        "reason": f"Approval required to run {tool_name}",
                    },
                },
            )

    async def _finalize_completed(self, record: RunRecord, result) -> None:
        usage = _extract_usage(result)
        record.input_tokens = usage["input_tokens"]
        record.output_tokens = usage["output_tokens"]
        record.cost_usd = _compute_cost(usage, self.config)
        record.final_output = str(result.final_output or "")
        record.turns_used = len(getattr(result, "raw_responses", []) or [])
        record.state = "completed"
        self.metrics.inc_run_completed()
        self.metrics.add_usage(
            record.input_tokens, record.output_tokens, record.cost_usd
        )
        await self.db.update_run(
            record.run_id,
            state="completed",
            finished_at=utcnow(),
            turns_used=record.turns_used,
            cost_usd=record.cost_usd,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            final_output=record.final_output,
        )
        # Mirror to transcript.
        self.session_store.append(
            record.session_id,
            {
                "type": "assistant",
                "content": record.final_output,
                "run_id": record.run_id,
            },
        )
        await self._update_session_totals(record)
        self._publish(
            record,
            {
                "event": "run_completed",
                "data": {
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "final_output": record.final_output,
                    "usage": {
                        "input_tokens": record.input_tokens,
                        "output_tokens": record.output_tokens,
                        "cost_usd": record.cost_usd,
                    },
                },
            },
        )
        # Close the stream for subscribers.
        self._close_subscribers(record)

    async def _finalize_failed(self, record: RunRecord, exc: Exception) -> None:
        code, retryable = _classify_error(exc)
        record.state = "failed"
        record.error_code = code
        record.error_message = str(exc)
        self.metrics.inc_run_failed()
        await self.db.update_run(
            record.run_id,
            state="failed",
            finished_at=utcnow(),
            error_code=code,
            error_message=str(exc),
        )
        self._publish(
            record,
            {
                "event": "run_failed",
                "data": {
                    "run_id": record.run_id,
                    "error": {
                        "code": code,
                        "message": str(exc),
                        "retryable": retryable,
                    },
                },
            },
        )
        self._close_subscribers(record)

    async def _finalize_cancelled(self, record: RunRecord) -> None:
        if record.state in ("completed", "failed", "cancelled"):
            return
        record.state = "cancelled"
        await self.db.update_run(
            record.run_id,
            state="cancelled",
            finished_at=utcnow(),
            error_code="cancelled",
            error_message="Run cancelled",
        )
        self._publish(
            record,
            {
                "event": "run_failed",
                "data": {
                    "run_id": record.run_id,
                    "error": {
                        "code": "cancelled",
                        "message": "Run cancelled",
                        "retryable": False,
                    },
                },
            },
        )
        self._close_subscribers(record)

    async def _update_session_totals(self, record: RunRecord) -> None:
        row = await self.db.get_session(record.session_id)
        if not row:
            return
        await self.db.update_session(
            record.session_id,
            total_cost_usd=(row.get("total_cost_usd") or 0) + record.cost_usd,
            total_input_tokens=(row.get("total_input_tokens") or 0) + record.input_tokens,
            total_output_tokens=(row.get("total_output_tokens") or 0) + record.output_tokens,
            message_count=(row.get("message_count") or 0) + 1,
        )

    def _close_subscribers(self, record: RunRecord) -> None:
        for q in list(record.subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


def _extract_usage(result) -> dict[str, int]:
    """Sum token usage across the run's raw responses."""
    tin = tout = 0
    for resp in getattr(result, "raw_responses", []) or []:
        usage = getattr(resp, "usage", None)
        if usage:
            tin += getattr(usage, "input_tokens", 0) or 0
            tout += getattr(usage, "output_tokens", 0) or 0
    return {"input_tokens": tin, "output_tokens": tout}


def _compute_cost(usage: dict[str, int], config: AppConfig) -> float:
    tin = usage["input_tokens"]
    tout = usage["output_tokens"]
    return (
        tin / 1_000_000 * config.llm.price_input_per_mtok
        + tout / 1_000_000 * config.llm.price_output_per_mtok
    )


def _extract_arguments(item) -> Any:
    raw = getattr(item, "arguments", None)
    if raw is None:
        raw = getattr(item, "input", None)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"raw": raw}
    return raw or {}


def _classify_error(exc: Exception) -> tuple[str, bool]:
    """Map an exception to (error_code, retryable)."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if "max_turns" in name.lower() or "max turns" in msg:
        return "max_turns_exceeded", False
    if "auth" in msg or "api key" in msg or "401" in msg:
        return "llm_auth_error", False
    if "429" in msg or "rate limit" in msg:
        return "llm_unavailable", True
    if "503" in msg or "overloaded" in msg:
        return "llm_unavailable", True
    if "connection" in msg or "timeout" in msg:
        return "llm_unavailable", True
    if "guardrail" in name.lower() or "tripwire" in msg:
        return "input_rejected", False
    return "internal_error", False
