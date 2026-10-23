"""Run lifecycle: budgets, cancellation, fan-out, persistence.

Framework-free by contract -- this package talks to
:class:`claudio_core.engine.RunEngine`, never to ADK directly. The engine
turns a :class:`~claudio_core.engine.RunRequest` into a stream of Claudio SSE
events; everything that is *not* about the model lives here:

* ``RunRecord`` -- the per-run ring buffer, subscriber set, cancel event and
  the mutable accumulators (cost, tokens, todo counter, deliverable flags).
* ``start_run`` / ``resume_run`` / ``cancel_run`` -- the run's public surface.
* ``subscribe`` / ``open_stream`` / ``close_stream`` -- reconnect machinery.
* the budget gate and the recovery loop (retry with backoff, fallback model).
* finalisation -- the rich ``run_completed`` summary, the JSONL transcript
  mirror, and persisting the accumulators to the session row.

Two things differ from the upstream SDK-coupled manager because the engine,
not this layer, now owns the model conversation:

1. The engine emits ``run_completed`` / ``run_failed`` / ``approval_required``
   as events rather than raising or returning a ``RunState``. The recovery loop
   reads those events instead of catching exceptions.
2. The engine keeps its own conversation state (ADK's session store), so the
   mid-run "interrupt, replace the conversation, restart" compaction the SDK
   allowed is not reachable from here. Compaction is therefore proactive (before
   the run) and the live gauge is driven by the ``context_usage`` the engine
   reports each turn. ``request_compact`` keeps its external contract but, with
   no active run, refreshes the gauge rather than rewriting the model context.
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

from claudio_core.context import context_usage_payload
from claudio_core.db import Database, utcnow
from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.events import EventName
from claudio_core.instructions import build_instructions
from claudio_core.llm import LlmCallError, openai_chat_completion
from claudio_core.settings import AppConfig
from claudio_core.tools.registry import select_tool_names

from ..errors import ApiError

logger = logging.getLogger(__name__)

RING_BUFFER_SIZE = 2000

# Maximum automatic mid-run compactions per run. Beyond this the threshold no
# longer triggers a restart (a rapid-refill guard against a compaction loop).
MAX_MID_RUN_COMPACTIONS = 5

# A file this small is treated as a stub, not a real deliverable.
MIN_DELIVERABLE_BYTES = 200

# Tools that produce a file the user should see on the completion card.
_WRITE_TOOL_NAMES = {"write_file", "edit_file", "create_file"}
# Read/lookup tools -- each call is "gathering" for the write-first tracker.
_READ_TOOL_NAMES = {"read_file", "list_dir", "web_fetch", "web_search"}

# Fed as the input when the run is restarted after a retry or a compaction. The
# engine's own conversation already holds the progress, so this just tells the
# agent to pick up where it left off rather than re-explore.
CONTINUE_PROMPT = (
    "Your context was just compacted. The summary above records everything done "
    "so far, including which steps are complete and what remains. "
    "Do NOT re-read files, re-list directories, or re-run commands that the "
    "summary says you have already done -- that repeats a loop and wastes your "
    "remaining budget. "
    "Take the NEXT concrete action toward the goal right now. If the next step "
    "is to create or change something, do it now instead of re-examining. "
    "Act on what you already know; do not re-analyze it."
)

# Fed as the input when a run completed but its task list still has open items.
# Unlike CONTINUE_PROMPT (which follows a compaction), this is a plain "you are
# not done, keep going" nudge: the conversation is intact, the work so far is
# real, and the agent should resume at the first unfinished step.
AUTO_CONTINUE_PROMPT = (
    "Your last turn ended but the task is not finished — these items are still "
    "open: {outstanding}. Do not re-explain, re-summarize, or re-read what you "
    "have already done. Take the next concrete action toward the first open "
    "item right now: write the file, run the command, make the change. Keep "
    "working through the list until every item is done, then update your task "
    "list to mark them done."
)

WRAPUP_MAX_CHARS = 20000

WRAPUP_INSTRUCTIONS = (
    "You hit the turn limit before finishing the task. Below you are given the "
    "original request, your task list, the files you have written, and a log of "
    "your recent work. Write the deliverable the user asked for NOW as your "
    "final answer -- the actual content (the analysis, the document, the code), "
    "not a description of what you would do. If you cannot complete it, write "
    "the best partial version you can and end with a clear 'What is done / What "
    "remains' section. Never reply that you lack information or that no request "
    "was provided -- use what is below and produce a usable artifact. Do not "
    "call any tools.\n\n"
)


class BudgetExceededError(Exception):
    """Raised when a session's cumulative spend reaches ``max_budget_usd``."""

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
    started_at: float = 0.0
    files_written: list[str] = field(default_factory=list)
    commands_run: int = 0
    # ToolContext accumulator mirror. The engine owns its own live context, so
    # the RunManager tracks these from the event stream and persists them to the
    # session row on completion; the next run reads them back into its prompt.
    turns_since_todo: int = 0
    deliverable_written: bool = False
    reads_since_write: int = 0
    last_context_tokens: int = 0
    # Mid-run compaction bookkeeping.
    compact_requested: asyncio.Event = field(default_factory=asyncio.Event)
    mid_run_compactions: int = 0


class RunManager:
    def __init__(
        self,
        config: AppConfig,
        db: Database,
        session_store,
        metrics,
        compactor,
        engine,
    ) -> None:
        self.config = config
        self.db = db
        self.session_store = session_store
        self.metrics = metrics
        self.compactor = compactor
        self.engine = engine
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
            record.started_at = time.time()
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
                record.started_at = time.time()
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
        # Ask the engine to stop too; it checks the run id between events.
        try:
            await self.engine.cancel(run_id)
        except Exception as exc:  # noqa: BLE001 - cancellation is best-effort
            logger.warning("engine.cancel(%s) failed: %s", run_id, exc)
        if record.task and not record.task.done():
            record.task.cancel()
        return True

    def get_record(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def get_active_run_for_session(self, session_id: str) -> RunRecord | None:
        """The most recent still-active run for a session, if any."""
        record: RunRecord | None = None
        for r in self._runs.values():
            if r.session_id == session_id and r.state in ("running", "awaiting_approval"):
                record = r
        return record

    def active_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.state == "running")

    async def request_compact(self, session_id: str) -> dict[str, Any]:
        """Compact a session, whether or not a run is active.

        With a run paused on an approval the request is refused (409): the
        conversation must be resolved before its context is rewritten. With a
        run in flight the request is recorded and the gauge refreshed. With no
        active run the persisted size is re-rendered as a fresh gauge.

        Note: the engine owns the live model conversation in its own store, so
        this does not rewrite what the model sees mid-run -- it keeps the
        external contract (a fresh ``context_usage`` payload) and the durable
        breaker state.
        """
        record = self.get_active_run_for_session(session_id)
        if record is not None and record.state == "awaiting_approval":
            raise ApiError(
                409,
                "awaiting_approval",
                "Resolve the pending approval before compacting this session",
            )
        if record is not None:
            record.compact_requested.set()
            return {"ok": True, "requested": True}
        row = await self.db.get_session(session_id)
        used = int((row or {}).get("last_context_tokens") or 0)
        auto_off = bool(self.compactor and self.compactor.is_disabled(session_id))
        return {
            "ok": True,
            "used_tokens": used,
            "free_tokens": self._free_tokens(used),
            "context_usage": context_usage_payload(
                used, self.config, "stored", auto_disabled=auto_off
            ),
        }

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
        """Register a live subscriber queue and return (queue, replay_events)."""
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

    # ---- fan-out --------------------------------------------------------

    def _publish(self, record: RunRecord, event: dict[str, Any]) -> None:
        """Append one event to the run's ring buffer and fan it out live.

        The ring buffer lets a reconnecting client replay what it missed; the
        subscriber queues deliver to clients already attached. A slow consumer
        whose queue is full is skipped rather than blocking the producer -- the
        ring buffer is the durable copy it can catch up from.
        """
        record.seq += 1
        record.events.append((record.seq, event))
        for q in list(record.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ---- request construction ------------------------------------------

    def _free_tokens(self, used: int) -> int:
        if self.compactor is None:
            return 0
        return max(0, int(self.compactor.context_window) - int(self.compactor.reserve) - used)

    def _model_spec(self, model_override: str | None = None) -> ModelSpec:
        return ModelSpec(
            model=model_override or self.config.llm.model,
            base_url=self.config.llm.base_url,
            api_key=self.config.llm.api_key or "local",
            temperature=float(self.config.llm.temperature),
            max_tokens=int(self.config.llm.max_tokens),
            timeout_seconds=float(self.config.llm.timeout_seconds),
            fallback_model=self.config.llm.fallback_model,
        )

    def _tool_policy(self, approval_mode: str | None) -> ToolPolicy:
        enabled = list(self.config.agent.tools.enabled)
        disabled = list(self.config.agent.tools.disabled or [])
        names = select_tool_names(enabled, disabled)
        mode = approval_mode or self.config.agent.approval_mode
        return ToolPolicy(
            enabled=tuple(names),
            disabled=tuple(disabled),
            approval_mode=mode,
            strict_mode=bool(self.config.agent.strict_tools),
        )

    def _build_request(
        self,
        record: RunRecord,
        user_input: str,
        approval_mode: str | None,
        *,
        model_override: str | None = None,
    ) -> RunRequest:
        workspace = Path(self.config.agent.workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        instructions = build_instructions(
            self.config,
            record.session_id,
            turns_since_todo=record.turns_since_todo,
            skill_entries=None,
            reads_since_write=record.reads_since_write,
            deliverable_written=record.deliverable_written,
        )
        return RunRequest(
            run_id=record.run_id,
            session_id=record.session_id,
            workspace=workspace,
            instructions=instructions,
            user_input=user_input,
            model=self._model_spec(model_override),
            tools=self._tool_policy(approval_mode),
            max_turns=int(self.config.agent.max_turns),
        )

    # ---- drive loops ----------------------------------------------------

    async def _drive_new_run(
        self, record: RunRecord, message: str, approval_mode: str | None
    ) -> None:
        try:
            budget = float(self.config.agent.max_budget_usd or 0.0)
            row = await self.db.get_session(record.session_id)
            base_cost = float((row or {}).get("total_cost_usd") or 0.0)
            if budget > 0 and base_cost >= budget:
                raise BudgetExceededError(base_cost, budget)
            self.metrics.inc_run_started()
            self._publish(
                record,
                {
                    "event": EventName.RUN_STARTED.value,
                    "data": {
                        "run_id": record.run_id,
                        "session_id": record.session_id,
                    },
                },
            )
            request = self._build_request(record, message, approval_mode)
            await self._run_with_recovery(record, request, base_cost)
        except asyncio.CancelledError:
            await self._finalize_cancelled(record)
        except Exception as exc:  # noqa: BLE001
            await self._finalize_failed(record, exc)

    async def _drive_resumed_run(
        self,
        record: RunRecord,
        state_json: str,
        approval_id: str,
        decision: str,
        rejection_message: str | None,
    ) -> None:
        try:
            budget = float(self.config.agent.max_budget_usd or 0.0)
            row = await self.db.get_session(record.session_id)
            base_cost = float((row or {}).get("total_cost_usd") or 0.0)
            if budget > 0 and base_cost >= budget:
                raise BudgetExceededError(base_cost, budget)
            self.metrics.inc_run_started()
            self._publish(
                record,
                {
                    "event": EventName.RUN_STARTED.value,
                    "data": {
                        "run_id": record.run_id,
                        "session_id": record.session_id,
                    },
                },
            )
            request = self._build_request(record, "", None)
            await self._run_resumed_with_recovery(
                record, request, state_json, decision, rejection_message, base_cost
            )
        except asyncio.CancelledError:
            await self._finalize_cancelled(record)
        except Exception as exc:  # noqa: BLE001
            await self._finalize_failed(record, exc)

    async def _run_with_recovery(
        self, record: RunRecord, request: RunRequest, base_cost: float
    ) -> None:
        """Drive a fresh run, recovering from transient failures.

        Strategies, each bounded so the loop always terminates:
        - retry with exponential backoff up to ``llm.retry_attempts``;
        - one fallback-model attempt, if ``llm.fallback_model`` is set.

        A ``context_overflow`` failure restarts from a continue prompt (bounded
        by ``MAX_MID_RUN_COMPACTIONS``). Budget and cancellation are terminal.
        """
        max_retries = max(0, int(self.config.llm.retry_attempts or 0))
        fallback = self.config.llm.fallback_model
        attempt = 0
        used_fallback = False
        # Auto-continue bookkeeping. Usage is accumulated across every
        # continuation so the single final completion reports the true total;
        # progress is tracked so a model that keeps "finishing" without moving
        # the task forward stops instead of looping forever.
        budget = float(self.config.agent.max_budget_usd or 0.0)
        max_continues = max(0, int(self.config.agent.max_auto_continues or 0))
        total_input = 0
        total_output = 0
        total_cost = 0.0
        auto_continues = 0
        prev_outstanding: int | None = None
        prev_files_written = 0
        while True:
            kind, data = await self._consume(record, request)
            if kind == "completed":
                usage = data.get("usage") or {}
                total_input += int(usage.get("input_tokens") or 0)
                total_output += int(usage.get("output_tokens") or 0)
                total_cost += float(usage.get("cost_usd") or 0.0)
                merged = {
                    **data,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                        "cost_usd": total_cost,
                    },
                }
                outstanding = await self._outstanding_todos(record.session_id)
                if not outstanding:
                    await self._finalize_completed(record, merged)
                    return
                # The model stopped with items still on its task list. Keep it
                # going until the plan is done, stopping only on a guard: the
                # continuation cap, no progress since the last check, or the
                # session budget running out.
                files_now = len(record.files_written)
                made_progress = (
                    prev_outstanding is None
                    or len(outstanding) < prev_outstanding
                    or files_now > prev_files_written
                )
                over_budget = budget > 0 and (base_cost + total_cost) >= budget
                if auto_continues >= max_continues or not made_progress or over_budget:
                    await self._finalize_completed(record, merged)
                    return
                auto_continues += 1
                prev_outstanding = len(outstanding)
                prev_files_written = files_now
                request = self._build_request(
                    record,
                    AUTO_CONTINUE_PROMPT.format(outstanding=", ".join(outstanding)),
                    None,
                )
                continue
            if kind == "approval":
                await self._handle_approval(record, data)
                return
            # kind == "failed"
            err = data.get("error") or {}
            code = err.get("code", "engine_error")
            retryable = bool(err.get("retryable", False))
            if code == "cancelled":
                await self._finalize_cancelled(record)
                return
            if code == "context_overflow":
                if record.mid_run_compactions >= MAX_MID_RUN_COMPACTIONS:
                    await self._finalize_failed(
                        record, code, err.get("message", "Context overflow")
                    )
                    return
                record.mid_run_compactions += 1
                self._publish(
                    record,
                    {
                        "event": EventName.RUN_COMPACTED.value,
                        "data": {
                            "reason": "overflow",
                            "attempt": record.mid_run_compactions,
                        },
                    },
                )
                request = self._build_request(record, CONTINUE_PROMPT, None)
                continue
            if retryable and attempt < max_retries:
                attempt += 1
                self.metrics.inc_retry()
                self._publish(
                    record,
                    {
                        "event": EventName.RUN_RESTARTING.value,
                        "data": {"reason": code, "attempt": attempt},
                    },
                )
                await asyncio.sleep(_backoff_seconds(attempt))
                request = self._build_request(record, CONTINUE_PROMPT, None)
                continue
            if not used_fallback and fallback:
                used_fallback = True
                self._publish(
                    record,
                    {
                        "event": EventName.RUN_RESTARTING.value,
                        "data": {"reason": "fallback", "attempt": attempt},
                    },
                )
                request = self._build_request(
                    record, CONTINUE_PROMPT, None, model_override=fallback
                )
                continue
            await self._finalize_failed(
                record, code, err.get("message", "Run failed")
            )
            return

    async def _run_resumed_with_recovery(
        self,
        record: RunRecord,
        request: RunRequest,
        state_json: str,
        decision: str,
        rejection_message: str | None,
        base_cost: float,
    ) -> None:
        """Drive a resumed run with the same retry/fallback recovery."""
        max_retries = max(0, int(self.config.llm.retry_attempts or 0))
        fallback = self.config.llm.fallback_model
        attempt = 0
        used_fallback = False
        state = state_json
        budget = float(self.config.agent.max_budget_usd or 0.0)
        max_continues = max(0, int(self.config.agent.max_auto_continues or 0))
        total_input = 0
        total_output = 0
        total_cost = 0.0
        auto_continues = 0
        prev_outstanding: int | None = None
        prev_files_written = 0
        while True:
            kind, data = await self._consume_resume(record, request, state)
            if kind == "completed":
                usage = data.get("usage") or {}
                total_input += int(usage.get("input_tokens") or 0)
                total_output += int(usage.get("output_tokens") or 0)
                total_cost += float(usage.get("cost_usd") or 0.0)
                merged = {
                    **data,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                        "cost_usd": total_cost,
                    },
                }
                outstanding = await self._outstanding_todos(record.session_id)
                if not outstanding:
                    await self._finalize_completed(record, merged)
                    return
                files_now = len(record.files_written)
                made_progress = (
                    prev_outstanding is None
                    or len(outstanding) < prev_outstanding
                    or files_now > prev_files_written
                )
                over_budget = budget > 0 and (base_cost + total_cost) >= budget
                if auto_continues >= max_continues or not made_progress or over_budget:
                    await self._finalize_completed(record, merged)
                    return
                auto_continues += 1
                prev_outstanding = len(outstanding)
                prev_files_written = files_now
                request = self._build_request(
                    record,
                    AUTO_CONTINUE_PROMPT.format(outstanding=", ".join(outstanding)),
                    None,
                )
                state = None
                continue
            if kind == "approval":
                await self._handle_approval(record, data)
                return
            err = data.get("error") or {}
            code = err.get("code", "engine_error")
            retryable = bool(err.get("retryable", False))
            if code == "cancelled":
                await self._finalize_cancelled(record)
                return
            if retryable and attempt < max_retries:
                attempt += 1
                self.metrics.inc_retry()
                self._publish(
                    record,
                    {
                        "event": EventName.RUN_RESTARTING.value,
                        "data": {"reason": code, "attempt": attempt},
                    },
                )
                await asyncio.sleep(_backoff_seconds(attempt))
                # A resumed retry continues the conversation with a continue
                # prompt rather than re-answering the old approval.
                request = self._build_request(record, CONTINUE_PROMPT, None)
                state = None
                continue
            if not used_fallback and fallback:
                used_fallback = True
                self._publish(
                    record,
                    {
                        "event": EventName.RUN_RESTARTING.value,
                        "data": {"reason": "fallback", "attempt": attempt},
                    },
                )
                request = self._build_request(
                    record, CONTINUE_PROMPT, None, model_override=fallback
                )
                state = None
                continue
            await self._finalize_failed(
                record, code, err.get("message", "Run failed")
            )
            return

    async def _consume(self, record: RunRecord, request: RunRequest):
        """Forward one engine stream, returning the terminal outcome.

        Returns ``("completed", data)`` / ``("approval", data)`` /
        ``("failed", data)``. Non-terminal events are published live and folded
        into the record's accumulators.
        """
        async for evt in self.engine.stream(request):
            if record.cancel_event.is_set():
                return ("cancelled", {})
            name = evt.get("event")
            data = evt.get("data") or {}
            if name == EventName.RUN_COMPLETED.value:
                return ("completed", data)
            if name == EventName.RUN_FAILED.value:
                return ("failed", data)
            if name == EventName.APPROVAL_REQUIRED.value:
                return ("approval", data)
            self._track(record, name, data)
            self._publish(record, evt)
        return (
            "failed",
            {
                "error": {
                    "code": "engine_ended",
                    "message": "Engine stream ended without a terminal event",
                    "retryable": False,
                }
            },
        )

    async def _consume_resume(self, record: RunRecord, request: RunRequest, state):
        """Forward one engine resume stream, returning the terminal outcome."""
        async for evt in self.engine.resume(request, state):
            if record.cancel_event.is_set():
                return ("cancelled", {})
            name = evt.get("event")
            data = evt.get("data") or {}
            if name == EventName.RUN_COMPLETED.value:
                return ("completed", data)
            if name == EventName.RUN_FAILED.value:
                return ("failed", data)
            if name == EventName.APPROVAL_REQUIRED.value:
                return ("approval", data)
            self._track(record, name, data)
            self._publish(record, evt)
        return (
            "failed",
            {
                "error": {
                    "code": "engine_ended",
                    "message": "Engine resume stream ended without a terminal event",
                    "retryable": False,
                }
            },
        )

    def _track(self, record: RunRecord, name: str, data: dict[str, Any]) -> None:
        """Fold one forwarded event into the record's accumulators."""
        if name == EventName.TOOL_CALL.value:
            _track_produced(record, data)
            tool = data.get("name") or ""
            if tool in _WRITE_TOOL_NAMES:
                record.deliverable_written = True
                record.reads_since_write = 0
            elif tool in _READ_TOOL_NAMES:
                record.reads_since_write += 1
            elif tool == "todo_write":
                record.turns_since_todo = 0
        elif name == EventName.TOOL_RESULT.value:
            self._mirror_tool_event(record, {"event": name, "data": data})
        elif name == EventName.REASONING.value:
            # Persist reasoning to the JSONL transcript so it survives a view
            # remount (tab switch / reload). Without this the live reasoning is
            # only in the SSE stream and disappears when the chat view remounts
            # while the run is still going. Consecutive deltas are appended as
            # separate entries and merged back into one block on the client.
            text = data.get("text") or ""
            if text:
                try:
                    self.session_store.append(
                        record.session_id,
                        {
                            "type": "reasoning",
                            "content": text,
                            "run_id": record.run_id,
                        },
                    )
                except OSError:
                    logger.warning(
                        "failed to persist reasoning for run %s", record.run_id
                    )
        elif name == EventName.CONTEXT_USAGE.value:
            used = int(data.get("used_tokens") or 0)
            if used:
                record.last_context_tokens = used
            record.turns_since_todo += 1

    # ---- approval -------------------------------------------------------

    async def _handle_approval(self, record: RunRecord, data: dict[str, Any]) -> None:
        """Persist a pause on a tool confirmation and surface it to the client.

        The engine's ``approval_required`` carries the ADK confirmation id in
        ``approval_id``; that id is what ``engine.resume`` needs, so it is
        stored in the run's resume state. A separate row is written to the
        approvals table for the pending-approvals list.
        """
        # The engine's approval_required carries the ADK confirmation id, which
        # is what engine.resume needs to feed the function_response back. The
        # client, however, resolves via /approvals/{id}, which looks the row up
        # by its own generated id -- so the event must carry the DB id, not the
        # ADK one. Keep the ADK confirmation id only in the resume state.
        confirmation_id = data.get("approval_id") or ""
        tool = data.get("tool") or ""
        raw_args = data.get("arguments") or "{}"
        reason = data.get("reason") or f"Approval required to run {tool}"
        record.state = "awaiting_approval"
        record.resume_state_json = json.dumps(
            {
                "kind": "adk_confirmation",
                "confirmation_id": confirmation_id,
                "tool_name": tool,
                "arguments": _parse_args(raw_args),
                "hint": reason,
            }
        )
        await self.db.update_run(
            record.run_id,
            resume_state_json=record.resume_state_json,
            state="awaiting_approval",
        )
        approval_id = await self.db.create_approval(
            run_id=record.run_id,
            session_id=record.session_id,
            tool_name=tool,
            call_id=confirmation_id,
            arguments=_parse_args(raw_args),
            reason=reason,
        )
        # The engine already emitted approval_required as the terminal event of
        # this stream; forward it so the client sees it.
        self._publish(
            record,
            {
                "event": EventName.APPROVAL_REQUIRED.value,
                "data": {
                    "approval_id": approval_id,
                    "tool": tool,
                    "arguments": raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                    "reason": reason,
                },
            },
        )

    # ---- finalisation ---------------------------------------------------

    async def _finalize_completed(self, record: RunRecord, data: dict[str, Any]) -> None:
        duration = max(0.0, time.time() - (record.started_at or time.time()))
        usage = data.get("usage") or {}
        record.final_output = str(data.get("final_output") or "")
        record.input_tokens = int(usage.get("input_tokens") or record.last_context_tokens or 0)
        record.output_tokens = int(usage.get("output_tokens") or max(1, len(record.final_output) // 4))
        record.cost_usd = float(
            usage.get("cost_usd")
            or _compute_cost(record.input_tokens, record.output_tokens, self.config)
        )
        record.state = "completed"
        self.metrics.inc_run_completed()
        self.metrics.add_usage(record.input_tokens, record.output_tokens, record.cost_usd)
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
        todos = await self._load_todos(record.session_id)
        outstanding = [
            t.get("content", "")
            for t in todos
            if isinstance(t, dict) and t.get("status") != "done"
        ]
        deliverable_written = bool(record.files_written)
        if record.files_written:
            workspace = Path(self.config.agent.workspace_dir).resolve()
            deliverable_ok, deliverable_bytes, deliverable_details = _verify_deliverable(
                list(record.files_written), workspace
            )
        else:
            deliverable_ok, deliverable_bytes, deliverable_details = True, 0, []
        todos_done = (len(outstanding) == 0) if todos else deliverable_written
        task_complete = todos_done and deliverable_ok
        self.session_store.append(
            record.session_id,
            {
                "type": "assistant",
                "content": record.final_output,
                "run_id": record.run_id,
            },
        )
        self._append_turn_status(
            record,
            "completed",
            files_written=list(record.files_written),
            commands_run=record.commands_run,
            duration_seconds=round(duration, 1),
            cost_usd=record.cost_usd,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            outstanding_todos=outstanding,
            deliverable_written=deliverable_written,
            deliverable_ok=deliverable_ok,
            deliverable_bytes=deliverable_bytes,
            deliverable=deliverable_details,
            task_complete=task_complete,
        )
        await self._update_session_totals(record)
        self._publish(
            record,
            {
                "event": EventName.RUN_COMPLETED.value,
                "data": {
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "final_output": record.final_output,
                    "usage": {
                        "input_tokens": record.input_tokens,
                        "output_tokens": record.output_tokens,
                        "cost_usd": record.cost_usd,
                    },
                    "summary": {
                        "files_written": list(record.files_written),
                        "commands_run": record.commands_run,
                        "duration_seconds": round(duration, 1),
                        "outstanding_todos": outstanding,
                        "deliverable_written": deliverable_written,
                        "deliverable_ok": deliverable_ok,
                        "deliverable_bytes": deliverable_bytes,
                        "deliverable": deliverable_details,
                        "task_complete": task_complete,
                    },
                },
            },
        )
        self._close_subscribers(record)

    async def _finalize_failed(self, record: RunRecord, code: str | Exception, message: str | None = None) -> None:
        duration = max(0.0, time.time() - (record.started_at or time.time()))
        if isinstance(code, Exception):
            code, _retryable = _classify_error(code)
            message = _sanitize_error_message(code)
        record.state = "failed"
        record.error_code = code
        record.error_message = message or ""
        self.metrics.inc_run_failed()
        await self.db.update_run(
            record.run_id,
            state="failed",
            finished_at=utcnow(),
            error_code=code,
            error_message=record.error_message,
        )
        self._append_turn_status(
            record,
            "failed",
            error_code=code,
            error_message=record.error_message,
            duration_seconds=round(duration, 1),
        )
        self._publish(
            record,
            {
                "event": EventName.RUN_FAILED.value,
                "data": {
                    "run_id": record.run_id,
                    "error": {
                        "code": code,
                        "message": record.error_message,
                        "retryable": False,
                    },
                },
            },
        )
        self._close_subscribers(record)

    async def _finalize_cancelled(self, record: RunRecord) -> None:
        if record.state in ("completed", "failed", "cancelled"):
            return
        duration = max(0.0, time.time() - (record.started_at or time.time()))
        record.state = "cancelled"
        await self.db.update_run(
            record.run_id,
            state="cancelled",
            finished_at=utcnow(),
            error_code="cancelled",
            error_message="Run cancelled",
        )
        self._append_turn_status(
            record,
            "interrupted",
            duration_seconds=round(duration, 1),
        )
        self._publish(
            record,
            {
                "event": EventName.RUN_FAILED.value,
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
            last_context_tokens=record.last_context_tokens,
            turns_since_todo=record.turns_since_todo,
        )

    async def _load_todos(self, session_id: str) -> list[dict]:
        row = await self.db.get_session(session_id)
        raw = (row or {}).get("todos")
        if not raw:
            return []
        try:
            todos = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return todos if isinstance(todos, list) else []

    async def _outstanding_todos(self, session_id: str) -> list[str]:
        """The contents of every task-list item that is not yet done."""
        todos = await self._load_todos(session_id)
        return [
            t.get("content", "")
            for t in todos
            if isinstance(t, dict) and t.get("status") != "done"
        ]

    def _close_subscribers(self, record: RunRecord) -> None:
        for q in list(record.subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _append_turn_status(self, record: RunRecord, status: str, **extra: Any) -> None:
        entry: dict[str, Any] = {
            "type": "turn_status",
            "status": status,
            "run_id": record.run_id,
        }
        entry.update(extra)
        try:
            self.session_store.append(record.session_id, entry)
        except OSError:
            logger.warning("failed to persist turn_status for run %s", record.run_id)

    def _mirror_tool_event(self, record: RunRecord, sse: dict[str, Any]) -> None:
        event = sse.get("event")
        if event not in ("tool_call", "tool_result"):
            return
        entry: dict[str, Any] = dict(sse.get("data") or {})
        entry["type"] = event
        entry["run_id"] = record.run_id
        try:
            self.session_store.append(record.session_id, entry)
        except OSError:
            logger.warning("failed to mirror %s for run %s", event, record.run_id)


# --------------------------------------------------------------------------
# Module helpers
# --------------------------------------------------------------------------


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": raw}
        except (ValueError, TypeError):
            return {"raw": raw}
    return {}


def _compute_cost(input_tokens: int, output_tokens: int, config: AppConfig) -> float:
    return (
        input_tokens / 1_000_000 * config.llm.price_input_per_mtok
        + output_tokens / 1_000_000 * config.llm.price_output_per_mtok
    )


def _track_produced(record: RunRecord, data: dict[str, Any]) -> None:
    name = data.get("name") or ""
    if name in _WRITE_TOOL_NAMES:
        args = data.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = {}
        if isinstance(args, dict):
            path = args.get("path") or args.get("file_path") or args.get("file")
            if path:
                record.files_written.append(str(path))
    elif name == "run_command":
        record.commands_run += 1


def _verify_deliverable(
    files_written: list[str], workspace: Path
) -> tuple[bool, int, list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    max_bytes = 0
    ok = False
    for raw in files_written:
        p = Path(raw)
        target = p if p.is_absolute() else (workspace / p)
        try:
            size = target.stat().st_size
        except OSError:
            details.append({"path": raw, "bytes": 0, "status": "missing"})
            continue
        if size >= MIN_DELIVERABLE_BYTES:
            ok = True
        max_bytes = max(max_bytes, size)
        details.append(
            {
                "path": raw,
                "bytes": size,
                "status": "ok" if size >= MIN_DELIVERABLE_BYTES else "stub",
            }
        )
    return ok, max_bytes, details


def _backoff_seconds(attempt: int) -> float:
    base = min(500 * (2 ** (attempt - 1)), 30000) / 1000.0
    return base + random.uniform(0, 0.25)


def _sanitize_error_message(exc: Exception) -> str:
    text = str(exc)
    if "<html" in text.lower() or "<!doctype" in text.lower():
        import re

        text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    if not text:
        text = type(exc).__name__
    if len(text) > 280:
        text = text[:277] + "…"
    return text


def _is_context_overflow(exc: Exception) -> bool:
    m = str(exc).lower()
    return (
        "context length" in m
        or "maximum context" in m
        or "context_window" in m
        or "context size" in m
        or "prompt is too long" in m
        or "too many tokens" in m
        or "reduce the length" in m
    )


def _classify_error(exc: Exception) -> tuple[str, bool]:
    name = type(exc).__name__
    msg = str(exc).lower()
    if isinstance(exc, BudgetExceededError):
        return "budget_exceeded", False
    if "max_turns" in name.lower() or "max turns" in msg:
        return "max_turns_exceeded", False
    if _is_context_overflow(exc):
        return "context_overflow", False
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
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 500 <= status <= 599:
        return "llm_unavailable", True
    return "internal_error", False


__all__ = ["RunManager", "RunRecord", "BudgetExceededError"]
