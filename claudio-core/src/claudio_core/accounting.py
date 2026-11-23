"""Unified LLM-call accounting, tool telemetry and lifecycle counters.

Issue #58 (Performance Improvement Plan), Increment 0: without complete
accounting, optimizations can look successful while quietly moving work into
unreported retries, cache misses, compaction or background calls. This module
is the single ledger every provider call and every tool execution flows into.

Design constraints:

* **Framework-free.** Like the rest of ``claudio_core``, this module must not
  know about ADK. Callers hand it plain dicts / dataclasses.
* **One record per logical call.** A retry inside the HTTP layer is an
  *attempt* on one record, not two records -- physical HTTP posts and logical
  model calls must not be conflated.
* **Buffered, flushed at run boundaries.** The hot loop must not pay a SQLite
  commit per model turn; records accumulate per run and are persisted when the
  run finalises (or on demand for calls that outlive a run, e.g. learning).
* **Measured vs estimated.** Provider-reported usage is recorded as measured;
  when a provider omits usage the record is flagged ``measured=False`` so
  aggregates can tell the difference instead of silently mixing estimates in.

The prompt-cache signal the plan asks for is ``cache_hit_ratio``:
``cached_input_tokens / input_tokens`` over the measured calls of a run.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

#: Chars-per-token heuristic shared with the engine's context guard
#: (``adk_engine._GUARD_CHARS_PER_TOKEN``). Used only for *estimates* of tool
#: payload sizes; provider usage is always preferred when present.
CHARS_PER_TOKEN = 2.4

#: The purposes a model call can be tagged with. ``purpose`` is free-form text
#: in storage but new values should be added here so reports stay comparable.
PURPOSES = (
    "primary",        # the agent's own turn on a run
    "retry",          # a re-attempt of a failed turn (attempt > 1)
    "router",         # model-role classification
    "compaction",     # Tier-2 LLM summarisation
    "delegate",       # a delegated worker sub-agent
    "learning",       # post-run learning extraction
    "wrapup",         # the tool-less wrap-up pass after the turn cap
    "daemon",         # the autonomous scheduled-task agent
    "fixer",          # autonomous fixer proposals
    "consolidation",  # autonomous skill consolidation
    "probe",          # /config/llm/test connectivity probe
    "other",
)

#: Lifecycle counters tracked per run (Increment 0's "aggregate counters").
LIFECYCLE_COUNTERS = (
    "retries",
    "replans",
    "auto_continues",
    "tier1_compactions",
    "tier2_compactions",
    "model_calls",
    "tool_calls",
)


def estimate_tokens(text: str) -> int:
    """Estimate the token size of a text payload (chars / 2.4, rounded)."""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN + 0.5)


def overlap_ratio(records: list["ToolCallRecord"]) -> float:
    """How much wall-clock a set of tool calls actually shared.

    ``sum(duration) / span`` where ``span`` is the distance from the earliest
    start to the latest finish. A perfectly serial sequence gives ~1.0; a batch
    of four reads that genuinely ran together gives close to 4.0. Returns 0.0
    for an empty group or a zero-length span so callers can distinguish "no
    overlap measured" from "no calls".

    This is the Increment 0b signal for Increment 1: before bounded parallel
    execution lands, every group is serial and the ratio sits at ~1; a real
    read batch must push it above 1.
    """
    timed = [r for r in records if r.finished_at > r.started_at]
    if not timed:
        return 0.0
    span_s = max(r.finished_at for r in timed) - min(r.started_at for r in timed)
    if span_s <= 0:
        return 0.0
    busy_ms = sum(float(r.duration_ms or 0.0) for r in timed)
    return round(busy_ms / (span_s * 1000.0), 4)


def cache_hit_ratio(cached_input_tokens: int, input_tokens: int) -> float:
    """Prompt-cache hit ratio, clamped to [0, 1]; 0.0 when nothing measured."""
    if input_tokens <= 0:
        return 0.0
    return round(min(1.0, max(0.0, cached_input_tokens / input_tokens)), 4)


@dataclass
class LlmCallRecord:
    """One logical model call: primary turn, retry, router, compaction, ..."""

    run_id: str = ""
    session_id: str = ""
    purpose: str = "primary"
    model: str = ""
    model_role: str = ""
    profile_name: str = ""
    reasoning_effort: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: float = 0.0
    outcome: str = "ok"  # ok | error | cancelled
    error_code: str = ""
    attempt: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    measured: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolCallRecord:
    """One tool execution: timing, outcome and the size of its payload."""

    run_id: str = ""
    session_id: str = ""
    call_id: str = ""
    name: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: float = 0.0
    is_error: bool = False
    result_chars: int = 0
    est_result_tokens: int = 0
    concurrency_group: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CallAccountant:
    """Thread-safe per-run ledger of model calls, tool calls and lifecycle.

    Records are buffered in memory keyed by ``run_id`` and written to SQLite
    by :meth:`flush`, which the run manager calls at every terminal
    transition (completed / failed / cancelled) and after post-run capture.
    Aggregates (totals, cache-hit ratio, lifecycle counters) are computed from
    the buffer, so a run's numbers are complete the moment its last call
    returns -- even before the rows are on disk.
    """

    def __init__(self, metrics: Any = None) -> None:
        self._lock = threading.Lock()
        self._llm: dict[str, list[LlmCallRecord]] = {}
        self._tools: dict[str, list[ToolCallRecord]] = {}
        self._lifecycle: dict[str, dict[str, int]] = {}
        self.metrics = metrics
        # Uptime-scoped global aggregates for /monitoring/metrics.
        self.total_llm_calls = 0
        self.total_llm_errors = 0
        self.total_cached_input_tokens = 0
        self.total_input_tokens = 0

    # -- recording ---------------------------------------------------------

    def record_llm_call(self, rec: LlmCallRecord) -> None:
        with self._lock:
            self._llm.setdefault(rec.run_id, []).append(rec)
            self._life(rec.run_id)["model_calls"] += 1
            self.total_llm_calls += 1
            self.total_input_tokens += int(rec.input_tokens or 0)
            self.total_cached_input_tokens += int(rec.cached_input_tokens or 0)
            if rec.outcome == "error":
                self.total_llm_errors += 1
        m = self.metrics
        if m is not None:
            try:
                if rec.duration_ms:
                    m.record_llm_latency(rec.duration_ms)
                m.add_llm_call_stats(
                    input_tokens=rec.input_tokens if rec.measured else 0,
                    cached_input_tokens=rec.cached_input_tokens if rec.measured else 0,
                    error=rec.outcome == "error",
                )
            except Exception:  # noqa: BLE001 - telemetry must never break a call
                pass

    def record_tool_call(self, rec: ToolCallRecord) -> None:
        with self._lock:
            self._tools.setdefault(rec.run_id, []).append(rec)
            self._life(rec.run_id)["tool_calls"] += 1

    def bump_lifecycle(self, run_id: str, counter: str, n: int = 1) -> None:
        with self._lock:
            life = self._life(run_id)
            life[counter] = life.get(counter, 0) + n

    # -- reading -----------------------------------------------------------

    def llm_calls(self, run_id: str) -> list[LlmCallRecord]:
        with self._lock:
            return list(self._llm.get(run_id, ()))

    def tool_calls(self, run_id: str) -> list[ToolCallRecord]:
        with self._lock:
            return list(self._tools.get(run_id, ()))

    def tool_groups(self, run_id: str) -> dict[str, list[ToolCallRecord]]:
        """Recorded tool calls grouped by ``concurrency_group`` ("" = serial)."""
        with self._lock:
            recs = list(self._tools.get(run_id, ()))
        out: dict[str, list[ToolCallRecord]] = {}
        for r in recs:
            out.setdefault(r.concurrency_group or "", []).append(r)
        return out

    def overlap_ratio(self, run_id: str, concurrency_group: str | None = None) -> float:
        """Measured concurrency for a run (or one batch). See :func:`overlap_ratio`."""
        if concurrency_group is None:
            with self._lock:
                recs = list(self._tools.get(run_id, ()))
        else:
            recs = [
                r for r in self.tool_calls(run_id) if r.concurrency_group == concurrency_group
            ]
        return overlap_ratio(recs)

    def lifecycle(self, run_id: str) -> dict[str, int]:
        with self._lock:
            return dict(self._lifecycle.get(run_id, {}))

    def usage_totals(self, run_id: str) -> dict[str, Any]:
        """Token/cost totals and cache-hit ratio for one run's recorded calls.

        Only *measured* calls contribute token counts; the ratio is computed
        over measured input tokens so unmeasured calls cannot dilute it.
        """
        with self._lock:
            recs = list(self._llm.get(run_id, ()))
        tin = sum(r.input_tokens for r in recs if r.measured)
        tout = sum(r.output_tokens for r in recs if r.measured)
        cached = sum(r.cached_input_tokens for r in recs if r.measured)
        reasoning = sum(r.reasoning_tokens for r in recs if r.measured)
        cost = sum(r.cost_usd for r in recs)
        errors = sum(1 for r in recs if r.outcome == "error")
        return {
            "input_tokens": tin,
            "output_tokens": tout,
            "cached_input_tokens": cached,
            "reasoning_tokens": reasoning,
            "cost_usd": round(cost, 8),
            "calls": len(recs),
            "errors": errors,
            "cache_hit_ratio": cache_hit_ratio(cached, tin),
        }

    def global_totals(self) -> dict[str, Any]:
        with self._lock:
            return {
                "llm_calls_total": self.total_llm_calls,
                "llm_errors_total": self.total_llm_errors,
                "cached_input_tokens_total": self.total_cached_input_tokens,
                "input_tokens_total": self.total_input_tokens,
                "prompt_cache_hit_ratio": cache_hit_ratio(
                    self.total_cached_input_tokens, self.total_input_tokens
                ),
            }

    def forget(self, run_id: str) -> None:
        """Drop a run's buffer (used by tests and after a successful flush)."""
        with self._lock:
            self._llm.pop(run_id, None)
            self._tools.pop(run_id, None)
            self._lifecycle.pop(run_id, None)

    # -- persistence ---------------------------------------------------------

    async def flush(self, db: Any, run_id: str | None = None) -> int:
        """Write buffered rows to SQLite. Returns the number of LLM rows.

        ``run_id=None`` flushes every run's buffer. Each run is written as one
        unit and detached from the buffer only after its write commits, so a
        failure on one run re-queues *that* run (and only that run) for the
        next flush -- already-committed runs are never written twice. A failed
        flush re-raises so the caller can react, but never duplicates rows.
        """
        with self._lock:
            run_ids = list(self._llm) if run_id is None else [run_id]
        n = 0
        for rid in run_ids:
            with self._lock:
                recs = self._llm.pop(rid, [])
                tool_recs = self._tools.pop(rid, [])
            try:
                if recs:
                    await db.add_llm_calls([r.as_dict() for r in recs])
                    n += len(recs)
                if tool_recs:
                    await db.add_tool_calls([r.as_dict() for r in tool_recs])
            except Exception:
                # Only this run's rows are re-queued; earlier runs already
                # committed and must not be written again.
                with self._lock:
                    if recs:
                        self._llm[rid] = recs + self._llm.get(rid, [])
                    if tool_recs:
                        self._tools[rid] = tool_recs + self._tools.get(rid, [])
                raise
            # Fully written: drop the run's lifecycle bookkeeping too so the
            # accountant does not grow unbounded over the process lifetime.
            with self._lock:
                self._lifecycle.pop(rid, None)
        return n

    # -- sink factories ------------------------------------------------------

    def make_llm_sink(
        self,
        *,
        run_id: str = "",
        session_id: str = "",
        purpose: str = "other",
        model: str = "",
        model_role: str = "",
        profile_name: str = "",
        reasoning_effort: str = "",
    ) -> Callable[[dict[str, Any]], None]:
        """Build the ``usage_sink`` callable for :func:`claudio_core.llm`.

        The sink is called exactly once per logical call with a payload dict
        (see ``llm.openai_chat_completion`` docstring) and turns it into a
        buffered :class:`LlmCallRecord`.
        """

        def sink(payload: dict[str, Any]) -> None:
            usage = payload.get("usage") or {}
            # The payload's own identity fields win over the closure defaults:
            # the engine-path recorder is shared across runs, so each call
            # carries its run_id/session_id/role in the payload (issue #58
            # review). The closure values are the fallback for sinks built per
            # call (router/compaction/learning) that don't set them.
            rec = LlmCallRecord(
                run_id=str(payload.get("run_id") or run_id),
                session_id=str(payload.get("session_id") or session_id),
                purpose=payload.get("purpose") or purpose,
                model=payload.get("model") or model,
                model_role=str(payload.get("model_role") or model_role),
                profile_name=str(payload.get("profile_name") or profile_name),
                reasoning_effort=str(payload.get("reasoning_effort") or reasoning_effort),
                started_at=float(payload.get("started_at") or 0.0),
                finished_at=float(payload.get("finished_at") or 0.0),
                duration_ms=float(payload.get("duration_ms") or 0.0),
                outcome=str(payload.get("outcome") or "ok"),
                error_code=str(payload.get("error_code") or ""),
                attempt=int(payload.get("attempt") or 1),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
                reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                measured=bool(payload.get("measured", bool(usage))),
            )
            self.record_llm_call(rec)

        return sink

    def make_engine_recorder(self) -> Callable[[dict[str, Any]], None]:
        """Recorder callable for the engine-path HTTP boundary.

        ``reasoning_llm`` calls it with the same payload shape as the llm.py
        sink (see :meth:`make_llm_sink`); each payload becomes one record.
        """
        return self.make_llm_sink(purpose="primary")

    # -- internals -----------------------------------------------------------

    def _life(self, run_id: str) -> dict[str, int]:
        life = self._lifecycle.get(run_id)
        if life is None:
            life = {c: 0 for c in LIFECYCLE_COUNTERS}
            self._lifecycle[run_id] = life
        for c in LIFECYCLE_COUNTERS:
            life.setdefault(c, 0)
        return life


# --------------------------------------------------------------------------
# Process-wide default recorder (daemon / probe calls that outlive a run)
# --------------------------------------------------------------------------

_DEFAULT_RECORDER: Callable[[dict[str, Any]], None] | None = None


def set_default_recorder(fn: Callable[[dict[str, Any]], None] | None) -> None:
    """Install the fallback recorder used when a call carries no own sink."""
    global _DEFAULT_RECORDER
    _DEFAULT_RECORDER = fn


def get_default_recorder() -> Callable[[dict[str, Any]], None] | None:
    return _DEFAULT_RECORDER


__all__ = [
    "CHARS_PER_TOKEN",
    "PURPOSES",
    "LIFECYCLE_COUNTERS",
    "estimate_tokens",
    "cache_hit_ratio",
    "LlmCallRecord",
    "ToolCallRecord",
    "CallAccountant",
    "set_default_recorder",
    "get_default_recorder",
]
