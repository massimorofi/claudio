"""Bounded parallel read execution and the deterministic turn-boundary reducer.

Issue #58 (Performance Improvement Plan), Increment 1.

Two problems are solved here, and they belong together: a model that asks for
four independent reads should wait for one read, and the conversation state
that results must not depend on which of those reads happened to finish first.

* **Scheduling.** :func:`run_tool_batch` takes the tool calls of one turn and
  splits them by the registry's concurrency class. ``read_only`` calls that are
  not approval-gated run together, bounded by a hard semaphore
  (``agent.max_read_concurrency``, default 4, clamped to
  :data:`HARD_MAX_READ_CONCURRENCY`). Everything else -- ``state_mutating``
  (write/edit/run_command/todo_write/delegate) and ``exclusive`` (rewind) --
  stays strictly sequential, executed one at a time in request order. A
  mutating tool can never enter the parallel path: the classifier is the only
  door, and it is asserted by tests.

* **Ordering.** Results are collected by request index and returned sorted by
  that index, so the model always sees them in the order it asked for them no
  matter what order they completed in.

* **Determinism.** :func:`reduce_tool_context` folds a batch into the
  conversation state at the turn boundary. Its output is canonical: sorted by
  request index, deduplicated on (tool, argument digest), size-capped by an
  explicit character budget, and cancellation-aware (aborted calls become
  compact status markers, never missing entries). No wall-clock, no set or dict
  iteration order, no float timings reach the output, so two runs with the same
  inputs produce byte-identical context.

* **Cancellation.** A cooperative ``asyncio.Event`` aborts in-flight reads
  (their coroutines are cancelled) and prevents not-yet-started ones from
  starting. Already-completed results are kept and reduced exactly as above.

* **Telemetry.** Every call in a batch is recorded into the Increment 0 ledger
  with a shared ``concurrency_group`` so :func:`claudio_core.accounting.overlap_ratio`
  can show real overlap (> 1) instead of an accidental serial run.

Framework-free by design (no ADK import), like the rest of ``claudio_core``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .accounting import ToolCallRecord, estimate_tokens
from .context import ToolContext
from .tools.registry import TOOL_FUNCTIONS, TOOL_META, needs_approval_for

__all__ = [
    "DEFAULT_MAX_READ_CONCURRENCY",
    "HARD_MAX_READ_CONCURRENCY",
    "REDUCER_MAX_CHARS",
    "REDUCER_ENTRY_MAX_CHARS",
    "ToolRequest",
    "ToolResult",
    "BatchOutcome",
    "ReducedTurn",
    "NonParallelToolError",
    "classify_tool",
    "is_parallel_safe",
    "resolve_read_bound",
    "run_tool_batch",
    "run_parallel_reads_only",
    "reduce_tool_context",
    "apply_tool_context",
    "current_concurrency_group",
]

#: Used when the config does not carry ``agent.max_read_concurrency``.
DEFAULT_MAX_READ_CONCURRENCY = 4
#: Hard ceiling on the read semaphore. The plan allows 4-8; above this a burst
#: of reads just swaps CPU for memory pressure, so the bound is not negotiable.
HARD_MAX_READ_CONCURRENCY = 8
#: Default character budget for one reduced turn-boundary block.
REDUCER_MAX_CHARS = 24_000
#: Per-entry cap inside the reducer budget, so one huge file cannot evict a
#: whole batch of small results.
REDUCER_ENTRY_MAX_CHARS = 4_000

#: Statuses that mean "this call did not produce a usable payload".
CANCELLED_STATUSES = frozenset({"cancelled", "skipped"})

# Set while a batch is executing so tool telemetry can tag its records.
_GROUP_VAR: str = ""


def current_concurrency_group() -> str:
    """The concurrency group of the batch currently executing ("" if none)."""
    return _GROUP_VAR


@dataclass
class ToolRequest:
    """One requested tool call, before scheduling."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    index: int = 0
    call_id: str = ""

    @property
    def args_digest(self) -> str:
        return _digest(self.args)


@dataclass
class ToolResult:
    """The outcome of one request, tagged with its ORIGINAL request index."""

    index: int
    name: str
    call_id: str = ""
    args_digest: str = ""
    status: str = "ok"  # ok | error | cancelled | skipped
    payload: Any = None
    duration_ms: float = 0.0
    concurrency_class: str = "read_only"
    parallel: bool = False
    concurrency_group: str = ""
    result_chars: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class BatchOutcome:
    """Everything one turn's tool batch produced, in request order."""

    results: list[ToolResult] = field(default_factory=list)
    concurrency_group: str = ""
    bound: int = DEFAULT_MAX_READ_CONCURRENCY
    max_observed_concurrency: int = 0
    parallel_count: int = 0
    sequential_count: int = 0
    cancelled: bool = False

    def by_index(self) -> dict[int, ToolResult]:
        return {r.index: r for r in self.results}


@dataclass
class ReducedTurn:
    """Canonical, byte-stable fold of a batch into conversation state."""

    payload: dict[str, Any] = field(default_factory=dict)
    entries: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[int] = field(default_factory=list)
    text: str = ""
    digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.payload


class NonParallelToolError(RuntimeError):
    """Raised when a caller forces a non-read-only tool onto the parallel path."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _digest(value: Any) -> str:
    """Stable short hash of an arbitrary JSON-ish value."""
    try:
        blob = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive
        blob = repr(value)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(value)


def classify_tool(ctx: ToolContext | None, name: str) -> str:
    """Concurrency class actually used for scheduling this call.

    Returns ``"read_only"`` only when the tool is registered as read-only *and*
    the approval policy would not pause on it. Anything approval-gated is
    demoted to ``"approval_gated"`` so it runs sequentially after the human
    decision, never inside the parallel fan-out.
    """
    meta = TOOL_META.get(name)
    if meta is None:
        return "state_mutating"
    mode = getattr(ctx, "approval_mode", "off") if ctx is not None else "off"
    if needs_approval_for(meta, mode):
        return "approval_gated"
    return getattr(meta, "concurrency", "read_only")


def is_parallel_safe(ctx: ToolContext | None, name: str) -> bool:
    """True only for side-effect-free, non-gated tools."""
    return classify_tool(ctx, name) == "read_only"


def resolve_read_bound(ctx: ToolContext | None = None, bound: int | None = None) -> int:
    """Effective read concurrency: explicit ``bound`` > run's semaphore > config.

    The per-run :class:`~claudio_core.context.RunConcurrency` wins over config
    because it is the semaphore actually guarding the run: reporting a different
    number than the one being enforced would make the telemetry lie.
    """
    if bound is None:
        conc = getattr(ctx, "concurrency", None) if ctx is not None else None
        if conc is not None and getattr(conc, "max_read_concurrency", 0):
            bound = conc.max_read_concurrency
        else:
            agent = getattr(getattr(ctx, "config", None), "agent", None)
            bound = getattr(agent, "max_read_concurrency", DEFAULT_MAX_READ_CONCURRENCY)
    try:
        value = int(bound)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_READ_CONCURRENCY
    return max(1, min(HARD_MAX_READ_CONCURRENCY, value))


# ---------------------------------------------------------------------------
# batch execution
# ---------------------------------------------------------------------------


def _normalize(
    requests: Iterable[ToolRequest | dict[str, Any]],
) -> list[ToolRequest]:
    """Accept dataclasses or plain dicts and assign stable request indices."""
    out: list[ToolRequest] = []
    for i, item in enumerate(requests):
        if isinstance(item, ToolRequest):
            req = ToolRequest(item.name, dict(item.args), i, item.call_id or f"c{i}")
        elif isinstance(item, dict):
            req = ToolRequest(
                str(item.get("name", "")),
                dict(item.get("args") or {}),
                i,
                str(item.get("call_id") or f"c{i}"),
            )
        else:  # pragma: no cover - defensive
            raise TypeError(f"unsupported tool request: {item!r}")
        out.append(req)
    return out


def _record(
    ctx: ToolContext | None,
    res: ToolResult,
    *,
    group: str,
    record: bool,
) -> None:
    """Push one batch entry into the Increment 0 ledger (never fatal)."""
    accountant = getattr(ctx, "accountant", None) if ctx is not None else None
    if accountant is None or not record:
        return
    try:
        accountant.record_tool_call(
            ToolCallRecord(
                run_id=getattr(ctx, "run_id", "") or "",
                session_id=getattr(ctx, "session_id", "") or "",
                call_id=res.call_id,
                name=res.name,
                started_at=res.started_at,
                finished_at=res.finished_at,
                duration_ms=res.duration_ms,
                is_error=res.status in ("error",),
                result_chars=res.result_chars,
                est_result_tokens=estimate_tokens(_stringify(res.payload)),
                concurrency_group=group,
            )
        )
    except Exception:  # noqa: BLE001 - telemetry must never break a run
        pass


async def _call_once(
    fn: Callable[..., Any],
    ctx: ToolContext | None,
    req: ToolRequest,
    cancel_event: asyncio.Event | None,
) -> tuple[str, Any, float, float, float]:
    """Run one tool coroutine, racing it against cancellation.

    Returns ``(status, payload, started_at, finished_at, duration_ms)``. An
    aborted call reports ``"cancelled"`` instead of raising, so the batch always
    produces one result per request.
    """
    started = time.time()
    if cancel_event is not None and cancel_event.is_set():
        now = time.time()
        return "skipped", None, started, now, 0.0

    coro = fn(ctx, **req.args) if ctx is not None else fn(**req.args)
    if cancel_event is None:
        try:
            payload = await coro
        except asyncio.CancelledError:
            now = time.time()
            return "cancelled", None, started, now, (now - started) * 1000.0
        except Exception as exc:  # noqa: BLE001 - tool errors are data
            now = time.time()
            return "error", {"error": str(exc)}, started, now, (now - started) * 1000.0
        now = time.time()
        return _status(payload), payload, started, now, (now - started) * 1000.0

    call = asyncio.ensure_future(coro)
    waiter = asyncio.ensure_future(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if call in done:
            waiter.cancel()
            exc = call.exception() if not call.cancelled() else None
            now = time.time()
            if exc is not None:
                return "error", {"error": str(exc)}, started, now, (now - started) * 1000.0
            return _status(call.result()), call.result(), started, now, (now - started) * 1000.0
        # Cancel requested while the call was in flight: abort it.
        call.cancel()
        with contextlib.suppress(BaseException):
            await call
        now = time.time()
        return "cancelled", None, started, now, (now - started) * 1000.0
    finally:
        waiter.cancel()


def _status(payload: Any) -> str:
    if isinstance(payload, dict) and (payload.get("error") or payload.get("ok") is False):
        return "error"
    return "ok"


async def run_tool_batch(
    ctx: ToolContext | None,
    requests: Iterable[ToolRequest | dict[str, Any]],
    *,
    bound: int | None = None,
    concurrency_group: str | None = None,
    cancel_event: asyncio.Event | None = None,
    functions: dict[str, Callable[..., Any]] | None = None,
    record: bool = True,
) -> BatchOutcome:
    """Execute a turn's tool calls: reads in parallel (bounded), the rest serial.

    Requests are walked in order. Consecutive read-only calls form a *wave* that
    runs concurrently under a semaphore; any mutating, exclusive or
    approval-gated call closes the wave (it is awaited first), then runs alone.
    That keeps the ordering the model asked for *and* keeps a read from observing
    a half-applied write that came before it.

    Results are returned sorted by the ORIGINAL request index regardless of
    completion order.
    """
    global _GROUP_VAR
    reqs = _normalize(requests)
    cap = resolve_read_bound(ctx, bound)
    table = functions if functions is not None else TOOL_FUNCTIONS
    if concurrency_group:
        batch_id = concurrency_group
    else:
        # Deterministic batch id: derived from the requests themselves, so a
        # replayed turn produces the same telemetry group (and the same
        # reducer bytes) as the original run.
        batch_id = _digest(
            [[r.name, r.args_digest] for r in reqs]
        )[:12]
    group_read = f"read:{batch_id}"
    outcome = BatchOutcome(concurrency_group=group_read, bound=cap)
    if not reqs:
        return outcome

    results: dict[int, ToolResult] = {}
    live = {"n": 0, "peak": 0}
    sem = asyncio.Semaphore(cap)

    def _make(req: ToolRequest, cls: str, parallel: bool) -> ToolResult:
        return ToolResult(
            index=req.index,
            name=req.name,
            call_id=req.call_id,
            args_digest=req.args_digest,
            concurrency_class=cls,
            parallel=parallel,
            concurrency_group=group_read if parallel else "",
        )

    async def _run_read(req: ToolRequest) -> None:
        res = _make(req, "read_only", True)
        if cancel_event is not None and cancel_event.is_set():
            res.status, res.started_at, res.finished_at = "skipped", time.time(), time.time()
            results[req.index] = res
            _record(ctx, res, group=res.concurrency_group, record=record)
            return
        async with sem:
            live["n"] += 1
            live["peak"] = max(live["peak"], live["n"])
            try:
                status, payload, s, f, dur = await _call_once(
                    table.get(req.name) or _missing(req.name), ctx, req, cancel_event
                )
            finally:
                live["n"] -= 1
        res.status, res.payload = status, payload
        res.started_at, res.finished_at, res.duration_ms = s, f, dur
        res.result_chars = len(_stringify(payload))
        results[req.index] = res
        _record(ctx, res, group=res.concurrency_group, record=record)

    async def _run_serial(req: ToolRequest, cls: str) -> None:
        res = _make(req, cls, False)
        conc = getattr(ctx, "concurrency", None) if ctx is not None else None
        runner = table.get(req.name) or _missing(req.name)
        if cancel_event is not None and cancel_event.is_set():
            res.status = "skipped"
            res.started_at = res.finished_at = time.time()
            results[req.index] = res
            _record(ctx, res, group="", record=record)
            return
        async def _go() -> tuple[str, Any, float, float, float]:
            if conc is None or cls == "read_only":
                return await _call_once(runner, ctx, req, cancel_event)
            if cls == "exclusive":
                async with conc.exclusive_lock:
                    return await _call_once(runner, ctx, req, cancel_event)
            async with conc.mutation_lock:
                return await _call_once(runner, ctx, req, cancel_event)

        status, payload, s, f, dur = await _go()
        res.status, res.payload = status, payload
        res.started_at, res.finished_at, res.duration_ms = s, f, dur
        res.result_chars = len(_stringify(payload))
        results[req.index] = res
        _record(ctx, res, group="", record=record)

    async def _flush(wave: list[ToolRequest]) -> None:
        if not wave:
            return
        if len(wave) == 1:
            await _run_read(wave[0])
        else:
            await asyncio.gather(*[_run_read(r) for r in wave])
        outcome.parallel_count += len(wave)

    wave: list[ToolRequest] = []
    prev_group = _GROUP_VAR
    _GROUP_VAR = group_read
    try:
        for req in reqs:
            cls = classify_tool(ctx, req.name)
            if cls == "read_only":
                wave.append(req)
                continue
            await _flush(wave)
            wave = []
            await _run_serial(req, cls)
            outcome.sequential_count += 1
        await _flush(wave)
    finally:
        _GROUP_VAR = prev_group

    outcome.max_observed_concurrency = live["peak"]
    outcome.cancelled = bool(cancel_event is not None and cancel_event.is_set())
    # ORIGINAL request order, always.
    outcome.results = [results[i] for i in sorted(results)]
    return outcome


def _missing(name: str) -> Callable[..., Any]:
    async def _fn(ctx: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"ok": False, "error": f"unknown tool: {name}"}

    return _fn


async def run_parallel_reads_only(
    ctx: ToolContext | None,
    requests: Iterable[ToolRequest | dict[str, Any]],
    **kwargs: Any,
) -> BatchOutcome:
    """Parallel-only entry point: refuses to schedule anything but reads.

    The strict exclusion test lives here: a mutating, exclusive or
    approval-gated tool raises :class:`NonParallelToolError` instead of being
    allowed onto the concurrent path. ``run_tool_batch`` is the scheduler the
    engine uses; this is the door callers use when they only mean to read.
    """
    reqs = _normalize(requests)
    bad = [r.name for r in reqs if not is_parallel_safe(ctx, r.name)]
    if bad:
        raise NonParallelToolError(
            "non-read-only tools cannot run on the parallel path: " + ", ".join(sorted(set(bad)))
        )
    return await run_tool_batch(ctx, reqs, **kwargs)


# ---------------------------------------------------------------------------
# deterministic turn-boundary reducer
# ---------------------------------------------------------------------------


def _entry(res: ToolResult, *, include_payload: bool) -> dict[str, Any]:
    """Canonical form of one result. Timings are deliberately excluded."""
    entry: dict[str, Any] = {
        "index": int(res.index),
        "name": res.name,
        "call_id": res.call_id,
        "args_digest": res.args_digest,
        "status": res.status if res.status in ("ok", "error", "cancelled", "skipped") else "error",
        "parallel": bool(res.parallel),
    }
    if include_payload:
        text = _stringify(res.payload)
        if len(text) > REDUCER_ENTRY_MAX_CHARS:
            text = text[:REDUCER_ENTRY_MAX_CHARS] + "...[truncated]"
        entry["payload"] = text
        entry["payload_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        entry["chars"] = len(text)
    else:
        entry["payload"] = ""
        entry["payload_sha256"] = ""
        entry["chars"] = 0
    return entry


def reduce_tool_context(
    results: Iterable[ToolResult],
    *,
    turn_id: str = "",
    max_chars: int = REDUCER_MAX_CHARS,
    dedupe: bool = True,
) -> ReducedTurn:
    """Fold a batch of tool results into canonical conversation state.

    Guarantees (all asserted by ``tests/test_issue58_concurrency.py``):

    * **sorted by request index** -- completion order never leaks;
    * **deduplicated** -- the same (tool, arguments) pair appears once, keeping
      the lowest request index;
    * **size-capped** -- entries are kept in index order while they fit
      ``max_chars``; the rest are listed in ``dropped`` rather than silently
      vanishing;
    * **cancellation-aware** -- cancelled/skipped calls are kept as compact
      status markers (no payload) and flip the ``cancelled`` flag, so a partial
      batch reduces to exactly the same bytes every time;
    * **byte-identical** -- the serialized form contains no timings, no
      randomness and no unordered containers.
    """
    items = sorted(results, key=lambda r: int(r.index))
    budget = max(0, int(max_chars))

    seen: set[tuple[str, str]] = set()
    kept: list[ToolResult] = []
    deduped: list[int] = []
    for res in items:
        key = (res.name, res.args_digest)
        if dedupe and key in seen:
            deduped.append(int(res.index))
            continue
        seen.add(key)
        kept.append(res)

    entries: list[dict[str, Any]] = []
    dropped: list[int] = []
    spent = 0
    for res in kept:
        cancelled_like = res.status in CANCELLED_STATUSES
        entry = _entry(res, include_payload=not cancelled_like)
        cost = len(entry["payload"])
        if spent + cost > budget:
            dropped.append(int(res.index))
            continue
        spent += cost
        entries.append(entry)

    counts = {
        "total": len(items),
        "kept": len(entries),
        "ok": sum(1 for e in entries if e["status"] == "ok"),
        "error": sum(1 for e in entries if e["status"] == "error"),
        "cancelled": sum(1 for e in entries if e["status"] in CANCELLED_STATUSES),
        "deduped": len(deduped),
        "dropped": len(dropped),
    }
    payload = {
        "version": 1,
        "turn_id": str(turn_id),
        "cancelled": any(e["status"] in CANCELLED_STATUSES for e in entries),
        "budget_chars": budget,
        "used_chars": spent,
        "counts": counts,
        "entries": entries,
        "dropped_indices": dropped,
        "deduped_indices": deduped,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return ReducedTurn(
        payload=payload,
        entries=entries,
        dropped=dropped,
        text=text,
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def apply_tool_context(
    ctx: ToolContext | None,
    results: Iterable[ToolResult],
    *,
    turn_id: str = "",
    max_chars: int = REDUCER_MAX_CHARS,
) -> ReducedTurn:
    """Reduce a batch and fold the canonical block into ``ctx`` conversation state."""
    reduced = reduce_tool_context(results, turn_id=turn_id, max_chars=max_chars)
    if ctx is not None:
        blocks = getattr(ctx, "tool_context_blocks", None)
        if isinstance(blocks, list):
            blocks.append(reduced.text)
        ctx.tool_context_digest = reduced.digest
        ctx.tool_context_text = reduced.text
        ctx.tool_context_entries = list(reduced.entries)
    return reduced
