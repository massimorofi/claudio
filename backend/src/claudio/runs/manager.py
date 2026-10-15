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
import random
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


class BudgetExceededError(Exception):
    """Raised when a session's cumulative spend reaches ``max_budget_usd``.

    Treated as a terminal, non-retryable failure by the recovery loop.
    """

    def __init__(self, spent: float, budget: float) -> None:
        super().__init__(
            f"Budget exceeded: ${spent:.4f} spent of ${budget:.4f} limit"
        )
        self.spent = spent
        self.budget = budget


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
            mode = approval_mode or self.config.agent.approval_mode
            agent = build_primary_agent(self.config, approval_mode=mode)
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
            await self._run_with_recovery(record, agent, message, tctx, session)
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
            await self._run_with_recovery(record, agent, state, tctx, session)
        except asyncio.CancelledError:
            await self._finalize_cancelled(record)
        except Exception as e:  # noqa: BLE001
            await self._finalize_failed(record, e)

    async def _load_state(self, agent, state_json: str):
        from agents import RunState

        data = json.loads(state_json) if isinstance(state_json, str) else state_json
        return await RunState.from_json(agent, data)

    async def _last_context_tokens(self, session_id: str) -> int:
        """The input-token size of the most recent completed turn (context proxy)."""
        row = await self.db.get_session(session_id)
        return int((row or {}).get("last_context_tokens") or 0)

    async def _maybe_compact(self, record: RunRecord, session) -> int:
        """Proactively compact the session if its current size nears the window.

        The current size is estimated from the session's text content so it works
        even with providers that don't report usage on the streamed path. Returns
        the (post-compaction) estimated token count.
        """
        items = await session.get_items()
        est = _estimate_items_tokens(items)
        if self.compactor.is_disabled(record.session_id):
            return est
        if self.compactor.should_compact(est):
            if await self.compactor.compact(
                session, _make_model(self.config), record.session_id
            ):
                items = await session.get_items()
                est = _estimate_items_tokens(items)
        return est

    async def _run_with_recovery(
        self, record: RunRecord, agent, input_data, tctx, session
    ) -> None:
        """Wrap a streamed run with compaction, budget, retry and fallback.

        Recovery strategies, each bounded so the loop always terminates:
        - **Proactive compaction** once, before the first attempt.
        - **Run-start budget gate** on the session's cumulative spend.
        - **Reactive compaction** once, on a context-overflow error.
        - **Retry with exponential backoff** up to ``llm.retry_attempts``.
        - **Fallback model** once, if ``llm.fallback_model`` is set.

        ``BudgetExceededError`` and cancellation are terminal (never retried).
        """
        max_retries = max(0, int(self.config.llm.retry_attempts or 0))
        budget = float(self.config.agent.max_budget_usd or 0.0)
        fallback = self.config.llm.fallback_model

        row = await self.db.get_session(record.session_id)
        base_cost = float((row or {}).get("total_cost_usd") or 0.0)

        # Run-start budget gate (per-session cumulative), before any work that
        # itself costs money (compaction included).
        if budget > 0 and base_cost >= budget:
            raise BudgetExceededError(base_cost, budget)

        # Proactive compaction once, before the first attempt.
        await self._maybe_compact(record, session)
        # Snapshot the session AFTER proactive compaction so a rollback restores
        # the compacted state. The SDK appends the run's input to the session
        # during the run, so a naive retry would otherwise double-append it.
        snapshot = await session.get_items()

        attempt = 0
        used_fallback = False
        used_reactive_compaction = False
        while True:
            try:
                await self._run_stream(
                    record, agent, input_data, tctx, session, base_cost
                )
                return  # completed, or paused for approval (normal)
            except asyncio.CancelledError:
                raise
            except BudgetExceededError:
                raise  # terminal, not retryable
            except Exception as exc:  # noqa: BLE001
                code, retryable = _classify_error(exc)

                # (1) Reactive compaction on context overflow — once per run.
                if (
                    _is_context_overflow(exc)
                    and not used_reactive_compaction
                    and not self.compactor.is_disabled(record.session_id)
                ):
                    used_reactive_compaction = True
                    if await self.compactor.compact(
                        session, _make_model(self.config), record.session_id
                    ):
                        snapshot = await session.get_items()
                        self._publish(
                            record,
                            {
                                "event": "run_restarting",
                                "data": {"reason": "compacted", "attempt": attempt},
                            },
                        )
                        continue

                # (2) App-level retry with exponential backoff on transient errors.
                if retryable and attempt < max_retries:
                    attempt += 1
                    self.metrics.inc_retry()
                    await session.clear_session()
                    await session.add_items(snapshot)
                    self._publish(
                        record,
                        {
                            "event": "run_restarting",
                            "data": {"reason": code, "attempt": attempt},
                        },
                    )
                    await asyncio.sleep(_backoff_seconds(attempt))
                    continue

                # (3) Fallback model, once, if configured.
                if not used_fallback and fallback:
                    used_fallback = True
                    agent = build_primary_agent(self.config, model_override=fallback)
                    await session.clear_session()
                    await session.add_items(snapshot)
                    self._publish(
                        record,
                        {
                            "event": "run_restarting",
                            "data": {"reason": "fallback", "attempt": attempt},
                        },
                    )
                    continue

                # Nothing left to try.
                raise

    async def _run_stream(
        self, record, agent, input_data, tctx, session, base_cost: float = 0.0
    ) -> None:
        """Drive a streamed run to completion, interruption, or failure.

        Checks the per-session budget after each turn (each completed response).
        """
        budget = float(self.config.agent.max_budget_usd or 0.0)
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
            # Mid-run budget check: each response.completed grows raw_responses.
            if budget > 0:
                spent = base_cost + _compute_cost(_extract_usage(result), self.config)
                if spent >= budget:
                    raise BudgetExceededError(spent, budget)

        # After the stream: interruptions (approval) or completion.
        interruptions = getattr(result, "interruptions", None)
        if interruptions:
            await self._handle_interruption(record, result)
            return

        await self._finalize_completed(record, result, session)

    async def _handle_interruption(self, record: RunRecord, result) -> None:
        state = result.to_state()
        state_dict = state.to_json(context_serializer=_serialize_tool_context)
        state_json = json.dumps(state_dict)
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
                        "arguments": json.dumps(arguments),
                        "reason": f"Approval required to run {tool_name}",
                    },
                },
            )

    async def _finalize_completed(self, record: RunRecord, result, session=None) -> None:
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
        # Persist the post-run context size. Prefer the model-reported input size;
        # fall back to a content estimate when the provider reports 0 (common on
        # the streamed path for local providers).
        last_in = _last_response_input_tokens(result, 0)
        if last_in <= 0 and session is not None:
            last_in = _estimate_items_tokens(await session.get_items())
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
        await self._update_session_totals(record, last_context_tokens=last_in)
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

    async def _update_session_totals(
        self, record: RunRecord, last_context_tokens: int | None = None
    ) -> None:
        row = await self.db.get_session(record.session_id)
        if not row:
            return
        fields: dict[str, Any] = {
            "total_cost_usd": (row.get("total_cost_usd") or 0) + record.cost_usd,
            "total_input_tokens": (row.get("total_input_tokens") or 0) + record.input_tokens,
            "total_output_tokens": (row.get("total_output_tokens") or 0) + record.output_tokens,
            "message_count": (row.get("message_count") or 0) + 1,
        }
        if last_context_tokens is not None:
            fields["last_context_tokens"] = last_context_tokens
        await self.db.update_session(record.session_id, **fields)

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


def _last_response_input_tokens(result, default: int = 0) -> int:
    """Input-token count of the most recent model response (full context sent).

    Some OpenAI-compatible providers (e.g. LM Studio) report zero usage on the
    streamed path; callers should fall back to :func:`_estimate_items_tokens`
    when this returns 0.
    """
    raw = getattr(result, "raw_responses", None) or []
    if not raw:
        return default
    usage = getattr(raw[-1], "usage", None)
    if not usage:
        return default
    return getattr(usage, "input_tokens", default) or default


def _iter_strings(obj: Any):
    """Yield every string value nested in a JSON-like structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


def _estimate_items_tokens(items: Any) -> int:
    """Rough token estimate from the total text content of the session items.

    Provider-independent (does not rely on the model reporting usage). Uses the
    common ~4 chars/token heuristic over all string content (message text, tool
    arguments, tool results).
    """
    total_chars = sum(len(s) for s in _iter_strings(items))
    return total_chars // 4


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff capped at 30s, plus a small jitter."""
    base = min(500 * (2 ** (attempt - 1)), 30000) / 1000.0
    return base + random.uniform(0, 0.25)


def _is_context_overflow(exc: Exception) -> bool:
    """Detect a model 'context length exceeded' style error."""
    m = str(exc).lower()
    return (
        "context length" in m
        or "maximum context" in m
        or "context_window" in m
        or "prompt is too long" in m
        or "too many tokens" in m
        or "reduce the length" in m
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


def _serialize_tool_context(ctx: Any) -> dict[str, Any]:
    """JSON-safe view of a :class:`ToolContext` for run-state persistence.

    The SDK serializes the run context when a run pauses for approval. The live
    ``Database`` (a sqlite connection) and the ``agent_factory`` callable cannot be
    serialized, so they are omitted here. This is safe: on resume the run manager
    builds a fresh ``ToolContext`` (with a live DB) and passes it as the run
    context, which overrides whatever was restored from this snapshot.
    """
    return {
        "session_id": ctx.session_id,
        "workspace": str(ctx.workspace),
        "approval_mode": ctx.approval_mode,
        "run_id": ctx.run_id,
        "cost_usd": ctx.cost_usd,
        "turns_since_todo": ctx.turns_since_todo,
    }


def _classify_error(exc: Exception) -> tuple[str, bool]:
    """Map an exception to (error_code, retryable)."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if isinstance(exc, BudgetExceededError):
        return "budget_exceeded", False
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
