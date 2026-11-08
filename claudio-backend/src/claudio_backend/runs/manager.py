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
2. The engine keeps its own conversation state (ADK's session store), so
   compaction goes through the engine's ``get_conversation_items`` /
   ``replace_conversation`` rather than an SDK handle. Mid-run compaction is
   real: a ``context_overflow`` failure, a manual request that lands while a
   run is in flight, or a last-turn size that has crossed the compaction
   threshold all cause the stored conversation to be summarised and replaced
   before the run restarts from the continue prompt. The live gauge is driven
   by the ``context_usage`` the engine reports each turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from claudio_core.context import context_usage_payload
from claudio_core.db import Database, utcnow
from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.events import EventName
from claudio_core.instructions import build_instructions
from claudio_core.learning import capture_run
from claudio_core.llm import LlmCallError, openai_chat_completion
from claudio_core.settings import AppConfig, LLMConfig, resolve_profile
from claudio_core.tools.registry import select_tool_names

from ..errors import ApiError
from ..model_health import profile_name_for_role
from ..paths import resolve_workspace_dir

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

# Fed as the input when the engine cuts a run at its turn cap. Unlike
# AUTO_CONTINUE_PROMPT -- a push-harder nudge that assumes the plan is fine
# and only the momentum ran out -- this treats the cap as evidence the
# strategy itself is failing: name what did not work, then take a different
# route instead of repeating it.
REPLAN_PROMPT = (
    "You hit the turn limit without finishing the task. The approach so far "
    "is not working -- repeating it will not get a different result. "
    "First, state plainly what you tried and why it failed. Then choose a "
    "DIFFERENT route: a different tool, a different technique, or a smaller "
    "decomposition of the problem. If you are missing knowledge, go get it: "
    "search the web, read the relevant docs, load a skill. Do not repeat a "
    "failed step unchanged. Break the remaining work into smaller verifiable "
    "steps and take the first one now."
)

# Failures that replanning cannot fix. Even in approval-off mode -- where the
# agent keeps going through recoverable failures -- these stop the run: a
# rejected credential, a rejected input, an exhausted budget or a corrupt
# resume state will fail exactly the same way on the next attempt.
_TERMINAL_FAILURE_CODES = frozenset(
    {
        "cancelled",
        "budget_exceeded",
        "llm_auth_error",
        "input_rejected",
        "bad_resume_state",
    }
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
    # Workspace this run operates on, bound at session creation and carried on
    # the record so the run never re-resolves it from (mutable) global config.
    workspace: Path = Path(".")
    workspace_id: str = ""
    state: str = "running"  # running | awaiting_approval | completed | failed | cancelled
    # Effective approval mode for this run, resolved once at start. The
    # recovery loop reads it to decide whether a failure is something to
    # replan around (off) or a reason to stop.
    approval_mode: str = "on_request"
    model_role: str = "primary"
    last_model: str | None = None
    parent_run_id: str | None = None
    worker_instructions: str | None = None
    worker_max_turns: int | None = None
    worker_tools: tuple[str, ...] | None = None
    adk_session_id: str | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=RING_BUFFER_SIZE))
    seq: int = 0
    subscribers: set = field(default_factory=set)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    state_changed: asyncio.Event = field(default_factory=asyncio.Event)
    turns_used: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    delegated_input_tokens: int = 0
    delegated_output_tokens: int = 0
    delegated_cost_usd: float = 0.0
    final_output: str = ""
    error_code: str | None = None
    error_message: str | None = None
    resume_state_json: str | None = None
    started_at: float = 0.0
    files_written: list[str] = field(default_factory=list)
    commands_run: int = 0
    tool_calls_seen: int = 0
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
        self.profile_health = getattr(engine, "profile_health", None)
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()
        # Fire-and-forget post-run capture tasks. Held here so the event loop's
        # weak reference doesn't let them be GC'd mid-flight; each discards
        # itself on completion.
        self._capture_tasks: set = set()

    # ---- public API -----------------------------------------------------

    async def start_run(
        self,
        run_id: str,
        session_id: str,
        message: str,
        approval_mode: str | None = None,
        *,
        workspace: Path | None = None,
        workspace_id: str = "",
        model_role: str = "primary",
        parent_run_id: str | None = None,
        worker_instructions: str | None = None,
        worker_max_turns: int | None = None,
        worker_tools: tuple[str, ...] | None = None,
        adk_session_id: str | None = None,
    ) -> RunRecord:
        async with self._lock:
            record = RunRecord(
                run_id=run_id,
                session_id=session_id,
                workspace=workspace if workspace is not None
                else resolve_workspace_dir(self.config),
                workspace_id=workspace_id,
                approval_mode=approval_mode or self.config.agent.approval_mode,
                model_role=model_role,
                parent_run_id=parent_run_id,
                worker_instructions=worker_instructions,
                worker_max_turns=worker_max_turns,
                worker_tools=worker_tools,
                adk_session_id=adk_session_id,
            )
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
        *,
        workspace: Path | None = None,
        workspace_id: str = "",
    ) -> RunRecord:
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                record = RunRecord(
                    run_id=run_id,
                    session_id=session_id,
                    workspace=workspace if workspace is not None
                    else resolve_workspace_dir(self.config),
                    workspace_id=workspace_id,
                    approval_mode=self.config.agent.approval_mode,
                )
                record.started_at = time.time()
                self._runs[run_id] = record
            record.state = "running"
            record.resume_state_json = state_json
            record.state_changed.set()
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
            child_run_ids = [
                child.run_id
                for child in self._runs.values()
                if child.parent_run_id == run_id
                and child.state in ("running", "awaiting_approval")
            ]
        # Ask the engine to stop too; it checks the run id between events.
        try:
            await self.engine.cancel(run_id)
        except Exception as exc:  # noqa: BLE001 - cancellation is best-effort
            logger.warning("engine.cancel(%s) failed: %s", run_id, exc)
        if record.task and not record.task.done():
            record.task.cancel()
        for child_run_id in child_run_ids:
            await self.cancel_run(child_run_id)
        return True

    def get_record(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def get_active_run_for_session(self, session_id: str) -> RunRecord | None:
        """The most recent still-active run for a session, if any."""
        record: RunRecord | None = None
        for r in self._runs.values():
            if (
                r.session_id == session_id
                and r.parent_run_id is None
                and r.state in ("running", "awaiting_approval")
            ):
                record = r
        return record

    def active_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.state == "running")

    def _build_summariser(self):
        """Build the injected summariser callable for the Compactor.

        Compaction is a single one-shot generation, not an agent run, so it
        goes straight through the OpenAI-compatible client rather than the
        engine. The summary budget and timeout come from the compactor so a
        slow local model gets the long budget its own config promises.
        """
        fast = resolve_profile(self.config, "fast")
        max_tokens = 2000
        if self.compactor is not None:
            max_tokens = int(getattr(self.compactor, "max_compact_tokens", 2000) or 2000)
        max_tokens = min(max_tokens, fast.context_window)
        timeout = float(self.config.agent.compaction_timeout_seconds)

        async def summarise(prompt: str) -> str:
            return await openai_chat_completion(
                base_url=fast.base_url,
                model=fast.model,
                messages=[{"role": "user", "content": prompt}],
                api_key=fast.api_key or "local",
                temperature=fast.temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )

        return summarise

    async def _resolve_workspace_id(self, row: dict[str, Any] | None) -> str:
        """Resolve the workspace id whose ADK state holds this session."""
        stored = (row or {}).get("workspace_dir") or ""
        if not stored:
            stored = str(resolve_workspace_dir(self.config))
        rec = await self.db.register_workspace(stored)
        return rec["id"]

    async def _auto_compact(self, record: RunRecord, reason: str) -> bool:
        """Compact the session while a run is in flight.

        Reads the conversation the engine holds, summarises it, replaces the
        stored conversation with the summary and persists the new size, then
        announces ``run_compacted`` so the client's gauge and toast follow.
        Returns True when the conversation was actually replaced.

        Failures are logged (and the breaker state persisted when the failure
        trips it) but never raised: the caller decides what an uncompacted
        overflow means, and a failed manual request should not kill the run.
        """
        if self.compactor is None or self.compactor.is_disabled(record.session_id):
            return False
        if record.mid_run_compactions >= MAX_MID_RUN_COMPACTIONS:
            return False
        record.mid_run_compactions += 1
        workspace_id = record.workspace_id
        if not workspace_id:
            row = await self.db.get_session(record.session_id)
            workspace_id = await self._resolve_workspace_id(row)
        try:
            items = await self.engine.get_conversation_items(
                record.session_id, workspace_id
            )
            if len(items) < 2:
                return False
            summarise = self._build_summariser()
            summary = await self.compactor.summarize(
                items, summarise, record.session_id
            )
        except Exception as exc:  # noqa: BLE001 - caller decides on failure
            logger.warning(
                "Mid-run compaction failed for session %s: %s", record.session_id, exc
            )
            if self.compactor.is_disabled(record.session_id):
                await self.db.update_session(record.session_id, compaction_disabled=1)
            return False
        if not summary or not str(summary).strip():
            return False
        await self.engine.replace_conversation(
            record.session_id, workspace_id, summary
        )
        new_used = max(1, len(summary) // 4)
        await self.db.update_session(
            record.session_id, last_context_tokens=new_used, compaction_disabled=0
        )
        record.last_context_tokens = new_used
        self._publish(
            record,
            {
                "event": EventName.RUN_COMPACTED.value,
                "data": {
                    "reason": reason,
                    "attempt": record.mid_run_compactions,
                    "used_tokens": new_used,
                    "free_tokens": self._free_tokens(new_used),
                },
            },
        )
        return True

    async def request_compact(self, session_id: str) -> dict[str, Any]:
        """Compact a session, whether or not a run is active.

        With a run paused on an approval the request is refused (409): the
        conversation must be resolved before its context is rewritten. With a
        run in flight the request is recorded and the gauge refreshed. With no
        active run the persisted conversation is actually summarised and
        replaced: the long transcript is dropped from the engine's store and
        the summary becomes the only prior context the next run sees, so the
        gauge drops to the summary's size.
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
        stored_used = int((row or {}).get("last_context_tokens") or 0)
        auto_off = bool(self.compactor and self.compactor.is_disabled(session_id))

        def _gauge(used: int, source: str, disabled: bool) -> dict[str, Any]:
            return {
                "used_tokens": used,
                "free_tokens": self._free_tokens(used),
                "context_usage": context_usage_payload(
                    used, self.config, source, auto_disabled=disabled
                ),
            }

        # Without a compactor there is nothing to summarise with; report the
        # stored gauge rather than crashing or falsely claiming a reduction.
        if self.compactor is None:
            return {
                "ok": False,
                "compacted": False,
                "error": "Compaction is not available",
                **_gauge(stored_used, "stored", auto_off),
            }

        # Read the model-facing conversation the engine actually holds.
        workspace_id = await self._resolve_workspace_id(row)
        items = await self.engine.get_conversation_items(session_id, workspace_id)

        # Nothing worth compacting: report the current gauge honestly rather
        # than claiming a reduction that did not happen.
        if len(items) < 2:
            return {
                "ok": True,
                "compacted": False,
                "reason": "nothing_to_compact",
                **_gauge(stored_used, "stored", auto_off),
            }

        summarise = self._build_summariser()
        try:
            summary = await self.compactor.summarize(items, summarise, session_id)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a failed compact
            logger.warning(
                "Manual compaction failed for session %s: %s", session_id, exc
            )
            disabled = bool(self.compactor and self.compactor.is_disabled(session_id))
            if disabled:
                await self.db.update_session(session_id, compaction_disabled=1)
            return {
                "ok": False,
                "compacted": False,
                "error": f"Compaction failed: {exc}",
                **_gauge(stored_used, "stored", disabled),
            }

        if not summary or not str(summary).strip():
            # summarize() guards against this, but never wipe a live
            # conversation on an empty summary.
            return {
                "ok": False,
                "compacted": False,
                "error": "Compaction produced an empty summary",
                **_gauge(stored_used, "stored", auto_off),
            }

        # The summariser took seconds; a run may have started while we were
        # summarising. Re-check under the lock before rewriting the store --
        # replacing the conversation out from under a live run would wipe the
        # context it is actively building. If one appeared, defer instead:
        # the run's own loop consumes ``compact_requested`` at its next
        # boundary.
        async with self._lock:
            racer = self.get_active_run_for_session(session_id)
            if racer is not None:
                racer.compact_requested.set()
                return {
                    "ok": True,
                    "requested": True,
                    "reason": "run_started_during_compact",
                    **_gauge(stored_used, "stored", auto_off),
                }
            await self.engine.replace_conversation(session_id, workspace_id, summary)
            new_used = max(1, len(summary) // 4)
            await self.db.update_session(
                session_id, last_context_tokens=new_used, compaction_disabled=0
            )
        return {
            "ok": True,
            "compacted": True,
            **_gauge(new_used, "compacted", False),
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

    def _attribute_event(
        self, record: RunRecord, request: RunRequest, event: dict[str, Any]
    ) -> dict[str, Any]:
        record.last_model = request.model.model
        attributed = dict(event)
        data = dict(attributed.get("data") or {})
        data.setdefault("model", request.model.model)
        data.setdefault("model_role", record.model_role)
        attributed["data"] = data
        return attributed

    def _mark_profile_outcome(
        self, record: RunRecord, *, error_code: str | None = None
    ) -> None:
        if not self.profile_health:
            return
        profile = profile_name_for_role(self.config, record.model_role)
        if error_code in ("llm_unavailable", "llm_auth_error"):
            self.profile_health.mark(profile, healthy=False, error_code=error_code)
        elif error_code is None:
            self.profile_health.mark(profile, healthy=True)

    # ---- request construction ------------------------------------------

    def _free_tokens(self, used: int) -> int:
        if self.compactor is None:
            return 0
        return max(0, int(self.compactor.context_window) - int(self.compactor.reserve) - used)

    def _model_spec(
        self, model_override: str | None = None, *, role: str = "primary"
    ) -> ModelSpec:
        profile = resolve_profile(self.config, role)
        # ``max_tokens: 0`` in the config means "use the default", not "send
        # zero". A literal 0 reaches the model as max_completion_tokens=0 and
        # every request fails with "integer below minimum value". The ModelSpec
        # default (4096) is too small for reasoning models, so fall back to the
        # LLMConfig default (16384).
        max_tokens = int(profile.max_tokens) or LLMConfig.model_fields[
            "max_tokens"
        ].default
        max_tokens = min(max_tokens, profile.context_window)
        return ModelSpec(
            model=model_override or profile.model,
            base_url=profile.base_url,
            api_key=profile.api_key or "local",
            temperature=float(profile.temperature),
            max_tokens=max_tokens,
            timeout_seconds=float(profile.timeout_seconds),
            fallback_model=self.config.llm.fallback_model,
            price_input_per_mtok=profile.price_input_per_mtok,
            price_output_per_mtok=profile.price_output_per_mtok,
        )

    def _model_spec_for_role(
        self, role: str, model_override: str | None = None
    ) -> ModelSpec:
        return self._model_spec(model_override, role=role)

    async def _auto_route_role(self, message: str) -> str:
        """Use the fast profile to route only clearly simple requests."""
        if not self.config.agent.auto_routing_enabled:
            return "primary"
        fast = resolve_profile(self.config, "fast")
        primary = resolve_profile(self.config, "primary")
        if (fast.model, fast.base_url) == (primary.model, primary.base_url):
            return "primary"
        profile_name = profile_name_for_role(self.config, "fast")
        if self.profile_health and self.profile_health.is_unavailable(profile_name):
            logger.warning(
                "auto-routing skipped: fast profile %s is unavailable",
                profile_name,
            )
            return "primary"
        text = message if len(message) <= 8000 else message[:4000] + "\n[...]\n" + message[-4000:]
        try:
            result = await openai_chat_completion(
                base_url=fast.base_url,
                model=fast.model,
                api_key=fast.api_key or "local",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Classify the request as FAST or PRIMARY. Reply with "
                            "one word only. FAST is only for a simple, low-risk "
                            "answer or one-step transformation that needs no tools. "
                            "Choose PRIMARY for coding, commands, tool use, "
                            "multi-step work, ambiguity, or anything needing "
                            "planning or judgment."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=512,
                timeout=min(float(fast.timeout_seconds), 15.0),
            )
        except Exception as exc:
            if self.profile_health:
                self.profile_health.mark(
                    profile_name, healthy=False, error_code="llm_unavailable"
                )
            logger.warning(
                "auto-routing failed for fast profile %s (%s); using primary",
                profile_name,
                type(exc).__name__,
            )
            return "primary"
        if self.profile_health:
            self.profile_health.mark(profile_name, healthy=True)
        decision = result.strip().upper().split()
        return "fast" if decision and decision[0] == "FAST" else "primary"

    async def _run_child_subagent(
        self, parent: RunRecord, *, agent: str, task: str, instructions: str
    ) -> dict[str, Any]:
        """Drive a worker as a normal run so its approvals can reach the user."""
        sub = self.config.agent.subagents.get(agent)
        if sub is None:
            return {"ok": False, "error": f"unknown subagent '{agent}'"}
        worker_profile = profile_name_for_role(self.config, "worker")
        fallback_reason: str | None = None
        role = (
            "primary"
            if self.profile_health
            and self.profile_health.is_unavailable(worker_profile)
            else "worker"
        )
        if role == "primary":
            fallback_reason = (
                f"Worker profile '{worker_profile}' is unavailable; using the primary model."
            )
            logger.warning(fallback_reason)

        async def launch(worker_role: str) -> RunRecord:
            child_id = str(uuid.uuid4())
            return await self.start_run(
                run_id=child_id,
                session_id=parent.session_id,
                message=task,
                approval_mode=parent.approval_mode,
                workspace=parent.workspace,
                workspace_id=parent.workspace_id,
                model_role=worker_role,
                parent_run_id=parent.run_id,
                worker_instructions=instructions,
                worker_max_turns=sub.max_turns,
                worker_tools=tuple(sub.tools),
                adk_session_id=f"worker-{child_id}",
            )

        child = await launch(role)
        while True:
            if child.state == "completed":
                return {
                    "ok": True,
                    "output": child.final_output,
                    "usage": {
                        "input_tokens": child.input_tokens,
                        "output_tokens": child.output_tokens,
                        "cost_usd": child.cost_usd,
                        "turns": child.turns_used,
                        "model": self._model_spec_for_role(role).model,
                        **(
                            {"fallback_reason": fallback_reason}
                            if fallback_reason
                            else {}
                        ),
                    },
                }
            if child.state in ("failed", "cancelled"):
                if (
                    role == "worker"
                    and child.state == "failed"
                    and child.error_code in ("llm_unavailable", "llm_auth_error")
                    and child.tool_calls_seen == 0
                ):
                    logger.warning(
                        "worker profile %s failed before tool execution; "
                        "retrying delegated task on primary",
                        worker_profile,
                    )
                    role = "primary"
                    fallback_reason = (
                        f"Worker profile '{worker_profile}' failed before execution; "
                        "the task was retried on the primary model."
                    )
                    child = await launch(role)
                    continue
                return {
                    "ok": False,
                    "error": child.error_message or "delegated worker run failed",
                    "usage": {
                        "input_tokens": child.input_tokens,
                        "output_tokens": child.output_tokens,
                        "cost_usd": child.cost_usd,
                        "turns": child.turns_used,
                        "model": self._model_spec_for_role(role).model,
                        **(
                            {"fallback_reason": fallback_reason}
                            if fallback_reason
                            else {}
                        ),
                    },
                }
            child.state_changed.clear()
            await child.state_changed.wait()

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
        # The workspace is bound at session creation and carried on the record;
        # never re-resolve it from (mutable) global config here, or a config
        # change mid-conversation would silently repoint the tools at a
        # different tree than the transcript's paths refer to.
        workspace = record.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        instructions = build_instructions(
            self.config,
            record.session_id,
            turns_since_todo=record.turns_since_todo,
            skill_entries=None,
            reads_since_write=record.reads_since_write,
            deliverable_written=record.deliverable_written,
            workspace=workspace,
        )
        if record.worker_tools is not None:
            tool_policy = ToolPolicy(
                enabled=record.worker_tools,
                disabled=(),
                approval_mode=approval_mode or record.approval_mode,
            )
        elif record.model_role == "fast":
            tool_policy = ToolPolicy(
                enabled=(),
                disabled=(),
                approval_mode=approval_mode or record.approval_mode,
                strict_mode=bool(self.config.agent.strict_tools),
            )
        else:
            tool_policy = self._tool_policy(approval_mode)
        return RunRequest(
            run_id=record.run_id,
            session_id=record.session_id,
            workspace=workspace,
            workspace_id=record.workspace_id,
            instructions=instructions,
            user_input=user_input,
            model=self._model_spec_for_role(record.model_role, model_override),
            tools=tool_policy,
            max_turns=record.worker_max_turns or int(self.config.agent.max_turns),
            metadata={
                "model_role": record.model_role,
                "profile_name": profile_name_for_role(
                    self.config, record.model_role
                ),
                "worker_instructions": record.worker_instructions,
                "adk_session_id": record.adk_session_id,
                "subagent_run_factory": (
                    lambda **kw: self._run_child_subagent(record, **kw)
                ),
            },
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
            if record.parent_run_id is None:
                record.model_role = await self._auto_route_role(message)
            request = self._build_request(record, message, approval_mode)
            self.metrics.inc_run_started()
            self._publish(
                record,
                {
                    "event": EventName.RUN_STARTED.value,
                    "data": {
                        "run_id": record.run_id,
                        "session_id": record.session_id,
                        "model": request.model.model,
                        "model_role": record.model_role,
                    },
                },
            )
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
            request = self._build_request(record, "", None)
            self.metrics.inc_run_started()
            self._publish(
                record,
                {
                    "event": EventName.RUN_STARTED.value,
                    "data": {
                        "run_id": record.run_id,
                        "session_id": record.session_id,
                        "model": request.model.model,
                        "model_role": record.model_role,
                    },
                },
            )
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
        replans = 0
        prev_outstanding: int | None = None
        prev_files_written = 0
        while True:
            # Boundary compaction: honour a manual request that landed while
            # this run was in flight, and proactively compact when the last
            # turn's reported size has crossed the threshold. Both keep the
            # next attempt small instead of waiting for a hard overflow.
            if record.compact_requested.is_set():
                record.compact_requested.clear()
                await self._auto_compact(record, "manual")
            elif (
                self.compactor is not None
                and record.last_context_tokens > 0
                and self.compactor.should_compact(record.last_context_tokens)
            ):
                await self._auto_compact(record, "proactive")
            kind, data = await self._consume(record, request)
            if kind == "completed":
                usage = data.get("usage") or {}
                total_input += int(usage.get("input_tokens") or 0)
                total_output += int(usage.get("output_tokens") or 0)
                total_cost += float(usage.get("cost_usd") or 0.0)
                record.turns_used += int(data.get("turns") or 0)
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
                over_budget = budget > 0 and (
                    base_cost + total_cost + record.delegated_cost_usd
                ) >= budget
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
            if kind == "cancelled":
                # The cancel event was observed between events; stop now.
                # Checked before the failure handling so the keep-going
                # replan path can never run over a user's cancellation.
                await self._finalize_cancelled(record)
                return
            # kind == "failed"
            err = data.get("error") or {}
            code = err.get("code", "engine_error")
            retryable = bool(err.get("retryable", False))
            if code == "cancelled":
                await self._finalize_cancelled(record)
                return
            if code == "max_turns_exceeded":
                # The engine cut the run at its turn cap. In approval-off
                # mode this is not a stop signal but a "change tack" signal:
                # replan with a fresh budget and keep going. The only
                # guardrail is the cost budget -- with budget 0 the run is
                # effectively unbounded, which is the point.
                over_budget = budget > 0 and (
                    base_cost + total_cost + record.delegated_cost_usd
                ) >= budget
                if record.approval_mode == "off" and not over_budget:
                    replans += 1
                    self._publish(
                        record,
                        {
                            "event": EventName.RUN_RESTARTING.value,
                            "data": {
                                "reason": "max_turns_replan",
                                "attempt": replans,
                            },
                        },
                    )
                    request = self._build_request(
                        record, REPLAN_PROMPT, record.approval_mode
                    )
                    continue
                # Not in keep-going mode (or out of budget): degrade
                # gracefully instead of ending on an error with nothing
                # produced.
                w_kind, w_data = await self._run_wrapup(record)
                if w_kind == "completed":
                    w_usage = w_data.get("usage") or {}
                    total_input += int(w_usage.get("input_tokens") or 0)
                    total_output += int(w_usage.get("output_tokens") or 0)
                    total_cost += float(w_usage.get("cost_usd") or 0.0)
                    record.turns_used += int(w_data.get("turns") or 0)
                    merged = {
                        **w_data,
                        "usage": {
                            "input_tokens": total_input,
                            "output_tokens": total_output,
                            "cost_usd": total_cost,
                        },
                    }
                    await self._finalize_completed(record, merged)
                elif w_kind == "cancelled":
                    await self._finalize_cancelled(record)
                else:
                    await self._finalize_failed(
                        record, code, err.get("message", "Run failed")
                    )
                return
            if code == "context_overflow":
                # Actually compact before restarting: summarise the stored
                # conversation and replace it, so the next attempt starts
                # from the summary instead of re-sending the same bloated
                # history and overflowing again.
                if not await self._auto_compact(record, "overflow"):
                    # Nothing was compacted -- the conversation was too
                    # short, the summariser failed, or the per-run cap was
                    # reached. Restarting would just overflow the same way,
                    # so stop with the original overflow message.
                    await self._finalize_failed(
                        record, code, err.get("message", "Context overflow")
                    )
                    return
                # The overflow is resolved; a pending manual request is
                # satisfied by the compaction we just performed.
                record.compact_requested.clear()
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
            # Approval-off mode means "find a way, no matter how long it
            # takes": a failure that isn't genuinely terminal is a reason
            # to change approach, not to give up. Back off between attempts
            # so a hard-down dependency doesn't spin the loop.
            if (
                record.approval_mode == "off"
                and code not in _TERMINAL_FAILURE_CODES
                and not (
                    budget > 0
                    and (
                        base_cost + total_cost + record.delegated_cost_usd
                    )
                    >= budget
                )
            ):
                replans += 1
                self._publish(
                    record,
                    {
                        "event": EventName.RUN_RESTARTING.value,
                        "data": {"reason": "replan", "attempt": replans},
                    },
                )
                await asyncio.sleep(_backoff_seconds(min(replans, 6)))
                request = self._build_request(
                    record, REPLAN_PROMPT, record.approval_mode
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
        replans = 0
        prev_outstanding: int | None = None
        prev_files_written = 0
        while True:
            # Same boundary compaction as the fresh-run loop: a manual
            # request that landed during the run, or a last-turn size over
            # the threshold, is applied before the next attempt.
            if record.compact_requested.is_set():
                record.compact_requested.clear()
                await self._auto_compact(record, "manual")
            elif (
                self.compactor is not None
                and record.last_context_tokens > 0
                and self.compactor.should_compact(record.last_context_tokens)
            ):
                await self._auto_compact(record, "proactive")
            kind, data = await self._consume_resume(record, request, state)
            if kind == "completed":
                usage = data.get("usage") or {}
                total_input += int(usage.get("input_tokens") or 0)
                total_output += int(usage.get("output_tokens") or 0)
                total_cost += float(usage.get("cost_usd") or 0.0)
                record.turns_used += int(data.get("turns") or 0)
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
                over_budget = budget > 0 and (
                    base_cost + total_cost + record.delegated_cost_usd
                ) >= budget
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
            if kind == "cancelled":
                await self._finalize_cancelled(record)
                return
            err = data.get("error") or {}
            code = err.get("code", "engine_error")
            retryable = bool(err.get("retryable", False))
            if code == "cancelled":
                await self._finalize_cancelled(record)
                return
            if code == "max_turns_exceeded":
                over_budget = budget > 0 and (
                    base_cost + total_cost + record.delegated_cost_usd
                ) >= budget
                if record.approval_mode == "off" and not over_budget:
                    replans += 1
                    self._publish(
                        record,
                        {
                            "event": EventName.RUN_RESTARTING.value,
                            "data": {
                                "reason": "max_turns_replan",
                                "attempt": replans,
                            },
                        },
                    )
                    request = self._build_request(
                        record, REPLAN_PROMPT, record.approval_mode
                    )
                    state = None
                    continue
                w_kind, w_data = await self._run_wrapup(record)
                if w_kind == "completed":
                    w_usage = w_data.get("usage") or {}
                    total_input += int(w_usage.get("input_tokens") or 0)
                    total_output += int(w_usage.get("output_tokens") or 0)
                    total_cost += float(w_usage.get("cost_usd") or 0.0)
                    record.turns_used += int(w_data.get("turns") or 0)
                    merged = {
                        **w_data,
                        "usage": {
                            "input_tokens": total_input,
                            "output_tokens": total_output,
                            "cost_usd": total_cost,
                        },
                    }
                    await self._finalize_completed(record, merged)
                elif w_kind == "cancelled":
                    await self._finalize_cancelled(record)
                else:
                    await self._finalize_failed(
                        record, code, err.get("message", "Run failed")
                    )
                return
            if code == "context_overflow":
                # Same real compaction as the fresh-run loop: replace the
                # bloated conversation before restarting from the continue
                # prompt, so the retry does not overflow again.
                if not await self._auto_compact(record, "overflow"):
                    await self._finalize_failed(
                        record, code, err.get("message", "Context overflow")
                    )
                    return
                record.compact_requested.clear()
                request = self._build_request(record, CONTINUE_PROMPT, None)
                state = None
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
            if (
                record.approval_mode == "off"
                and code not in _TERMINAL_FAILURE_CODES
                and not (
                    budget > 0
                    and (
                        base_cost + total_cost + record.delegated_cost_usd
                    )
                    >= budget
                )
            ):
                replans += 1
                self._publish(
                    record,
                    {
                        "event": EventName.RUN_RESTARTING.value,
                        "data": {"reason": "replan", "attempt": replans},
                    },
                )
                await asyncio.sleep(_backoff_seconds(min(replans, 6)))
                request = self._build_request(
                    record, REPLAN_PROMPT, record.approval_mode
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
                self._mark_profile_outcome(record)
                return ("completed", data)
            if name == EventName.RUN_FAILED.value:
                error = data.get("error") or {}
                self._mark_profile_outcome(
                    record, error_code=error.get("code")
                )
                return ("failed", data)
            if name == EventName.APPROVAL_REQUIRED.value:
                evt = self._attribute_event(record, request, evt)
                return ("approval", evt.get("data") or {})
            evt = self._attribute_event(record, request, evt)
            data = evt.get("data") or {}
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
                self._mark_profile_outcome(record)
                return ("completed", data)
            if name == EventName.RUN_FAILED.value:
                error = data.get("error") or {}
                self._mark_profile_outcome(
                    record, error_code=error.get("code")
                )
                return ("failed", data)
            if name == EventName.APPROVAL_REQUIRED.value:
                evt = self._attribute_event(record, request, evt)
                return ("approval", evt.get("data") or {})
            evt = self._attribute_event(record, request, evt)
            data = evt.get("data") or {}
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
            record.tool_calls_seen += 1
            _track_produced(record, data)
            # Persist the call itself, not just its result. The client rebuilds
            # tool cards and the workflow panel from tool_call entries (a later
            # tool_result only closes the pending card), so without this the
            # workflow panel and tool cards are empty after a view remount.
            self._mirror_tool_event(record, {"event": name, "data": data})
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
            if data.get("name") == "delegate_task":
                output = data.get("output")
                if isinstance(output, str):
                    try:
                        output = json.loads(output)
                    except (ValueError, TypeError):
                        output = None
                if isinstance(output, dict):
                    usage = output.get("usage")
                    if isinstance(usage, dict):
                        record.delegated_input_tokens += int(
                            usage.get("input_tokens") or 0
                        )
                        record.delegated_output_tokens += int(
                            usage.get("output_tokens") or 0
                        )
                        record.delegated_cost_usd += float(
                            usage.get("cost_usd") or 0.0
                        )
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
        record.state_changed.set()
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
        approval_event = {
            "event": EventName.APPROVAL_REQUIRED.value,
            "data": {
                "approval_id": approval_id,
                "run_id": record.run_id,
                "tool": tool,
                "arguments": raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                "reason": reason,
                "model": record.last_model or self._model_spec_for_role(record.model_role).model,
                "model_role": record.model_role,
            },
        }
        self._publish(record, approval_event)
        if record.parent_run_id:
            parent = self._runs.get(record.parent_run_id)
            if parent:
                self._publish(parent, approval_event)

    # ---- wrap-up --------------------------------------------------------

    async def _run_wrapup(self, record: RunRecord) -> dict[str, Any] | None:
        """One tool-less pass that writes out the partial deliverable.

        The turn cap cut the run short. Rather than ending on an error with
        nothing produced, give the agent a final turn whose only job is to
        write down whatever it has actually accomplished. The engine's own
        conversation holds the full history, so the wrap-up input only
        needs the durable summary the instruction refers to.

        Tools are stripped for this pass: the wrap-up must produce the
        deliverable as text, not start another round of work that could
        hit the cap again. Returns the terminal outcome of the wrap-up
        stream: ``("completed", data)`` on success, otherwise
        ``("failed"|"cancelled", data)``.
        """
        summary = await self._build_wrapup_summary(record)
        try:
            request = self._build_request(
                record, WRAPUP_INSTRUCTIONS + summary, "off"
            )
        except Exception as exc:  # noqa: BLE001 - wrap-up is best-effort
            logger.warning("could not build wrap-up request: %s", exc)
            return None
        request = replace(
            request,
            tools=ToolPolicy(
                enabled=(),
                disabled=request.tools.disabled,
                approval_mode="off",
                strict_mode=False,
            ),
            max_turns=2,
        )
        kind, data = await self._consume(record, request)
        if kind == "completed":
            return ("completed", data)
        if kind == "cancelled":
            return ("cancelled", {})
        return ("failed", data)

    async def _build_wrapup_summary(self, record: RunRecord) -> str:
        """The context block the wrap-up instruction promises the model."""
        todos = await self._load_todos(record.session_id)
        done = [
            t.get("content", "")
            for t in todos
            if isinstance(t, dict) and t.get("status") == "done"
        ]
        outstanding = [
            t.get("content", "")
            for t in todos
            if isinstance(t, dict) and t.get("status") != "done"
        ]
        parts: list[str] = []
        if done:
            parts.append("Completed:\n" + "\n".join(f"- {c}" for c in done))
        if outstanding:
            parts.append("Still open:\n" + "\n".join(f"- {c}" for c in outstanding))
        if record.files_written:
            parts.append("Files written: " + ", ".join(record.files_written))
        text = "\n\n".join(parts)
        if len(text) > WRAPUP_MAX_CHARS:
            text = text[:WRAPUP_MAX_CHARS]
        return text

    # ---- finalisation ---------------------------------------------------

    async def _finalize_completed(self, record: RunRecord, data: dict[str, Any]) -> None:
        duration = max(0.0, time.time() - (record.started_at or time.time()))
        usage = data.get("usage") or {}
        record.final_output = str(data.get("final_output") or "")
        (
            record.input_tokens,
            record.output_tokens,
            record.cost_usd,
        ) = _combined_usage(usage, record, self.config, record.final_output)
        record.state = "completed"
        record.state_changed.set()
        self.metrics.inc_run_completed()
        if record.parent_run_id is None:
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
            # Verify against the workspace bound to this run, not the current
            # global default — the files were written into the bound tree.
            deliverable_ok, deliverable_bytes, deliverable_details = _verify_deliverable(
                list(record.files_written), record.workspace
            )
        else:
            deliverable_ok, deliverable_bytes, deliverable_details = True, 0, []
        todos_done = (len(outstanding) == 0) if todos else deliverable_written
        task_complete = todos_done and deliverable_ok
        if record.parent_run_id is None:
            self.session_store.append(
                record.session_id,
                {
                    "type": "assistant",
                    "content": record.final_output,
                    "run_id": record.run_id,
                    "model": record.last_model or self._model_spec_for_role(record.model_role).model,
                    "model_role": record.model_role,
                },
            )
        if record.parent_run_id is None:
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
                    "turns": record.turns_used,
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
        if record.parent_run_id is None:
            await self._schedule_capture(record)
        self._close_subscribers(record)

    async def _schedule_capture(self, record: RunRecord) -> None:
        """Fire-and-forget the self-improvement capture pass for a finished run.

        Captures only the transcript entries appended since the last capture,
        tracked by a per-session watermark (``learning_captured_entries``).
        Reading just the new slice -- rather than the whole cumulative
        transcript -- is what stops a single fixed incident from being
        re-extracted on every later run, which would inflate its
        ``recurrence_count`` past the promotion threshold on phantom repeats.

        The watermark is advanced synchronously, before the capture task is
        spawned, so a subsequent run on this session sees the updated value
        immediately and never re-reads these entries -- regardless of how
        long the background extraction takes. Capture itself stays
        best-effort: capture_run swallows its own errors.
        """
        si = self.config.self_improvement
        if not (si.enabled and si.capture_enabled):
            return
        try:
            transcript = self.session_store.read(record.session_id)
            session = await self.db.get_session(record.session_id)
        except Exception as exc:  # noqa: BLE001 - capture is opportunistic
            logger.warning("capture: could not read transcript: %s", exc)
            return
        captured = int((session or {}).get("learning_captured_entries") or 0)
        # Clamp in case the transcript was cleared/truncated below the stored
        # watermark; a stale watermark must not skip future entries.
        start = min(captured, len(transcript))
        new_entries = transcript[start:]
        # Advance the watermark to the current length now, before handing the
        # slice off, so the next run never re-reads these entries. This runs
        # even when the slice is empty: if the transcript had been truncated
        # below a stale watermark, this corrects it so the next run resumes
        # from the right place instead of being stuck above the transcript.
        try:
            await self.db.update_session(
                record.session_id, learning_captured_entries=len(transcript)
            )
        except Exception as exc:  # noqa: BLE001 - capture is opportunistic
            logger.warning("capture: could not advance watermark: %s", exc)
            return
        if not new_entries:
            return
        task = asyncio.create_task(
            capture_run(
                self.db,
                new_entries,
                self.config,
                session_id=record.session_id,
                run_id=record.run_id,
            )
        )
        self._capture_tasks.add(task)
        task.add_done_callback(self._capture_tasks.discard)

    async def _finalize_failed(self, record: RunRecord, code: str | Exception, message: str | None = None) -> None:
        duration = max(0.0, time.time() - (record.started_at or time.time()))
        if isinstance(code, Exception):
            code, _retryable = _classify_error(code)
            message = _sanitize_error_message(code)
        record.state = "failed"
        record.state_changed.set()
        record.error_code = code
        record.error_message = message or ""
        self.metrics.inc_run_failed()
        await self.db.update_run(
            record.run_id,
            state="failed",
            finished_at=utcnow(),
            error_code=code,
            error_message=record.error_message,
            turns_used=record.turns_used,
        )
        if record.parent_run_id is None:
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
        record.state_changed.set()
        await self.db.update_run(
            record.run_id,
            state="cancelled",
            finished_at=utcnow(),
            error_code="cancelled",
            error_message="Run cancelled",
        )
        if record.parent_run_id is None:
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
    profile = resolve_profile(config, "primary")
    return (
        input_tokens / 1_000_000 * profile.price_input_per_mtok
        + output_tokens / 1_000_000 * profile.price_output_per_mtok
    )


def _combined_usage(
    usage: dict[str, Any],
    record: RunRecord,
    config: AppConfig,
    final_output: str,
) -> tuple[int, int, float]:
    primary_input_tokens = int(
        usage.get("input_tokens") or record.last_context_tokens or 0
    )
    primary_output_tokens = int(
        usage.get("output_tokens") or max(1, len(final_output) // 4)
    )
    primary_cost = usage.get("cost_usd")
    if primary_cost is None:
        primary_cost = _compute_cost(primary_input_tokens, primary_output_tokens, config)
    return (
        primary_input_tokens + record.delegated_input_tokens,
        primary_output_tokens + record.delegated_output_tokens,
        float(primary_cost) + record.delegated_cost_usd,
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
