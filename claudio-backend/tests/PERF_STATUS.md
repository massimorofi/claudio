# Performance Plan — Status

Issue #58 (Performance Improvement Plan) running status. One section per
increment; newest last. Each section records what changed, the gate that proves
it, and the green pytest summary line.

Gate convention (all increments):

```
cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \
  python3 -m pytest <explicit file paths> -p no:cacheprovider -o asyncio_mode=strict
```

Explicit file paths only — never `find`, never a glob that can match nothing (an
empty path makes pytest exit 2 before collecting, which is what silently sank the
previous round), and never the whole suite (`PERF_BASELINE.md` records 13
pre-existing failures outside this increment's scope: `test_issue54_gortex` 6,
`test_e2e` 4, `test_contract_shapes` 3). The gate forces `asyncio_mode=strict`,
so every async test carries `@pytest.mark.asyncio`; an unmarked async test is
*skipped*, not failed, which is how earlier rounds looked green and then failed.

## 0a(b)-i — exactly one accounting record per logical LLM call (incl. cancellation)

**Scope.** Every logical call through `claudio_core/llm.py::openai_chat_completion`
now emits **exactly one** accounting record — success, every error shape, and
cancellation — and a second emission is structurally impossible.

**What changed.**

1. `claudio-core/src/claudio_core/accounting.py` — new stdlib-only helper
   `once_sink(sink)`. It wraps a `usage_sink`/recorder callable and forwards at
   most one payload: the single-shot guard is set *before* forwarding (so even a
   raising sink cannot let a second record through), it is lock-protected (a call
   finishing on one thread while being cancelled on another still emits once), and
   `once_sink(None)` returns `None` so "no sink wired" behaviour is unchanged.
   Exported in `__all__`. The module stays free of third-party and relative
   imports (`threading`/`time`/`dataclasses`/`typing` only).
2. `claudio-core/src/claudio_core/llm.py` — `openai_chat_completion` wraps its
   sink at entry:
   `usage_sink = once_sink(usage_sink or get_default_recorder())`. Resolving the
   process-wide default recorder once at entry (instead of per emission) means
   every branch — including the no-explicit-sink path — shares a single emission
   budget, so no branch combination can double-count a call.
3. `claudio-core/src/claudio_core/llm.py` — added `except asyncio.CancelledError:`
   around the request. `CancelledError` derives from `BaseException`, so the
   existing `except Exception` net never caught it and a cancelled call was
   recorded **zero** times. It now emits one record with
   `outcome="cancelled", error_code="cancelled"` and re-raises unchanged, so
   cancellation semantics are untouched. Existing ok/error emissions and the
   `max_tokens`-rename retry semantics (one record, `attempt=2`) are unchanged.

**Tests.** `claudio-backend/tests/test_issue58_call_coverage.py` (fixed path, 17
tests, fully offline — `httpx.AsyncClient` is monkeypatched onto
`httpx.MockTransport`/a stdlib fake, so no credentials and no non-loopback
socket). One record asserted for each of: success, `no_base_url`, transport
error, HTTP >= 400, invalid JSON, `no_text`, cancellation (task cancelled while
awaiting the response), and the `max_tokens` rename retry (1 record,
`attempt == 2`); plus a double-rejection retry, a cancellation-after-emit race,
the default-recorder path, and `once_sink` units (forwards only once, stays
consumed when the sink raises, `None` passthrough, thread-safe under a race) and
"raising sink never breaks the call" for both the ok and the error path.

Regression proof: with the `llm.py` change stashed, the two cancellation tests
fail with `0 = len([])` — the exact zero-record bug — and the other 15 pass.

**Gate (green).**

```
cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \
  python3 -m pytest tests/test_issue58_call_coverage.py -p no:cacheprovider -o asyncio_mode=strict

============================== 17 passed in 0.14s ==============================
```

## 0a(b)-ia — engine-side boundaries record cancellations too

Increment 0a(b)-i closed the zero-record-on-cancellation and double-emission
holes on the *auxiliary* path (`claudio_core.llm.openai_chat_completion`). The
two **engine-side** boundaries still recorded nothing when a run was cancelled.
This increment extends the same invariant — one logical model call, exactly one
ledger record — to both of them.

**Changes.**

1. `claudio-backend/src/claudio_backend/engine/reasoning_llm.py` — the
   `chat.completions.create` wrapper installed by `_patch_max_tokens_retry` is
   the seam every engine HTTP call (primary turns, worker sub-agents, the daemon
   agent) passes through. Two fixes:
   * the resolved recorder is now wrapped in `accounting.once_sink` at call
     entry, so the `max_tokens` rename followed by a later error — or any other
     combination of emitting branches — cannot produce two records for one
     logical call;
   * an explicit `except asyncio.CancelledError:` branch around
     `chat.completions.create` emits one record with
     `outcome="cancelled", error_code="cancelled"` and re-raises unchanged.
     `CancelledError` derives from `BaseException`, so the existing
     `except Exception` net never saw it and a cancelled run emitted **zero**
     records.
2. `claudio-backend/src/claudio_backend/engine/reasoning_llm.py` —
   `ReasoningOpenAILlm._generate_content_streaming` shares one `once_sink` guard
   between its completion record and a new cancellation branch around stream
   consumption, so a stream cancelled mid-flight emits exactly one `cancelled`
   record while a stream that **completed and was then cancelled** cannot be
   recorded twice.
3. `claudio-backend/src/claudio_backend/engine/adk_engine.py` — the engine
   installs `accountant.make_engine_recorder()` into every call context, so
   boundary 1/2 already cover a call cancelled in flight. `_drive`'s
   `except asyncio.CancelledError` (and the cooperative `self._cancelled` path)
   now also call the new `_record_run_cancellation` backstop for cancellations
   the client boundary cannot see (queued, between calls, inside tool
   execution). Exactly-once is decided from the ledger itself: any existing
   primary/retry record — `ok` or `cancelled` — means the call was already
   accounted for, so only a run that would contribute *zero* records gets one,
   and a per-run `once_sink` guard makes a second emission structurally
   impossible.

**Tests.** `claudio-backend/tests/test_issue58_call_coverage_engine.py` (fixed
path, 18 tests, fully offline — a fake openai client and a fake recorder stand in
for the provider, so no credentials and no non-loopback socket). One record
asserted for each of: success (payload shape intact), the `max_tokens` rename
(1 record, `attempt == 2`), rename-then-error (1 record, `error`, `attempt == 2`),
a plain `BadRequestError`, a generic exception, cancellation during `create`,
cancellation during streaming, a completed-then-cancelled stream, a raising
recorder on both the ok and the error path, and the `once_sink` double-fire
guard. Plus the engine backstop: records once when the run has no records,
defers to a client-recorded `cancelled`, skips a completed-then-cancelled turn,
double-fires to one record, and no-ops without an accountant.

Regression proof: with the two `except asyncio.CancelledError` branches removed,
exactly the two cancellation tests fail with `assert 0 == 1` — the zero-record
bug — and the other 16 pass.

**Gate (green).**

```
cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \
  python3 -m pytest tests/test_issue58_call_coverage_engine.py -p no:cacheprovider -o asyncio_mode=strict

============================== 18 passed in 1.04s ==============================
```
