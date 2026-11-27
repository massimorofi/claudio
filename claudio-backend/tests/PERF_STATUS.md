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
