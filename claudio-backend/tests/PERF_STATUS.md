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

## 0a(b)-ii — every non-primary call site is purpose-tagged

Increment 0a(b)-i/-ia made one logical call emit exactly one record. This
increment closes the *coverage* half of the gap: the auxiliary paths (router,
Tier-2 compaction, learning, fixer, consolidation, the daemon and the
`/config/llm/test` probe) now each reach the ledger with the right `purpose`,
and with `run_id` / `session_id` / `model_role` filled in wherever they are
known. Nothing is left untagged, and nothing is tagged twice — the sink is
resolved once at the call entry point and `llm.openai_chat_completion` wraps it
in `accounting.once_sink`.

### Audited call sites

Every `openai_chat_completion(` / `chat.completions.create(` call site in
`claudio-core/src` and `claudio-backend/src`, as printed by
`python3 scripts/audit_llm_call_sites.py`:

| Site | Purpose | How it reaches the ledger |
| --- | --- | --- |
| `claudio-core/src/claudio_core/learning.py:175` | `learning` | `usage_sink=` from `runs/manager.py` (`make_llm_sink(purpose="learning")`, run/session filled); falls back to `get_default_recorder()` when no run is live |
| `claudio-backend/src/claudio_backend/api/config.py:78` (`run_llm_probe`, used by `POST /config/llm/test`) | `probe` | `usage_sink=` from the supervisor's `CallAccountant` (`model_role` = probed profile name), else `get_default_recorder()` |
| `claudio-backend/src/claudio_backend/autonomous/consolidation.py:139` (`_merge_categories`) | `consolidation` | `usage_sink=` (`make_llm_sink(purpose="consolidation", model_role="fast")`) else default recorder |
| `claudio-backend/src/claudio_backend/autonomous/consolidation.py:238` (`_draft_skill`) | `consolidation` | same as above |
| `claudio-backend/src/claudio_backend/autonomous/fixer.py:296` (`propose_fix`) | `fixer` | `usage_sink=` (`model_role="fast"`); each retry-loop generation is a *new* logical call, so it adds its own single record |
| `claudio-backend/src/claudio_backend/engine/reasoning_llm.py:312` (`chat.completions.create`, streaming) | `daemon` (engine path also carries `primary` / `retry` / `delegate`) | `_claudio_call_context["recorder"] or get_default_recorder()`, `once_sink`-wrapped per logical call; the daemon agent's context sets `purpose="daemon"` and its `session_id` |
| `claudio-backend/src/claudio_backend/runs/manager.py:450` (`_build_summariser`, Tier-2) | `compaction` | `usage_sink=` from `make_llm_sink(run_id, session_id, purpose="compaction", model_role="fast")` |
| `claudio-backend/src/claudio_backend/runs/manager.py:845` (`_auto_route_role`) | `router` | `usage_sink=` from `make_llm_sink(run_id, session_id, purpose="router", model_role="fast")` |
| `claudio-backend/src/claudio_backend/runs/manager.py:2100` (`extract_learnings` call) | `learning` | passes the purpose-tagged `learning_sink` down to the `learning.py` site above |

`claudio_core/llm.py:29` is the *definition* of `openai_chat_completion`, not a
call site, so it is not audited as one.

### Guard rails

* `scripts/audit_llm_call_sites.py` — stdlib-only (`ast` + `pathlib`) static
  audit. Walks both source trees, prints one line per audited call site, flags
  any site whose enclosing call lacks `usage_sink=` / `recorder=` / a
  default-recorder path, flags any literal `purpose=` outside
  `accounting.PURPOSES`, and ends with `audited=N untagged=0 bad_purpose=0`.
  Exits non-zero on any finding.
* `tests/test_issue58_aux_purposes.py` — drives each auxiliary helper with an
  in-memory `httpx.MockTransport` (or a stubbed SDK client for the daemon) and
  asserts **exactly one** record with the expected `purpose`, plus that a
  failure (`outcome="error"`) and a cancellation (`outcome="cancelled"`) of the
  same call still yield exactly one record. No network, no credentials.

### Verification

```
cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \
  python3 -m pytest tests/test_issue58_aux_purposes.py -p no:cacheprovider -o asyncio_mode=strict

============================== 28 passed in 1.06s ==============================

python3 scripts/audit_llm_call_sites.py

audited=8 untagged=0 bad_purpose=0 purposes_known=12
```

## 0a(c) — run totals are provably complete per purpose

**What changed.** `claudio_core.accounting` gained
`CallAccountant.usage_totals_by_purpose(run_id)` and
`CallAccountant.reconcile(run_id)`. The first returns one row per purpose
present in the run's ledger — `purpose`, `calls`, `errors`, `cancelled`,
`input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`,
`cost_usd`, `cache_hit_ratio` — computed from the *same* records with the *same*
rules as `usage_totals` (only measured calls contribute tokens; every call
counts as a call, error or cancellation). The second returns
`run_totals[field] - sum(per_purpose[field])` for every field in
`PURPOSE_TOTAL_FIELDS`; all of them are exactly zero, and `ok` says so in one
boolean. Purposes are never pre-seeded and no call is dropped: an unlabelled
call lands in `other`, so there is no unlabelled remainder to hide work in.

**Where it is surfaced.** `RunManager._fold_accounting` — the finalize path that
already called `usage_totals` — now also fills `record.by_purpose` (rows sorted
by calls desc) and `record.purpose_reconcile`, adds both to the dict it returns,
and the `run_completed` event carries them as additive keys
(`by_purpose`, `purpose_reconcile`), so anything reading a finished run's report
sees the breakdown next to the totals it reconciles against.
`claudio_backend.bench` gained `purpose_breakdown`,
`merge_breakdowns` and `render_purpose_markdown`, so a benchmark report carries
`by_purpose` / `reconcile` / `trials_by_purpose` next to the existing
`summarize()` keys. No existing key changed shape or meaning. The breakdown is
computed **before** `flush()`, which detaches the buffer.

**Sample.** The mixed ledger from `tests/test_issue58_purpose_totals.py`
(primary ok, primary error, retry at `attempt=2`, router, tier-2 compaction,
learning, delegate, a cancelled call, one of them unmeasured):

| purpose | calls | errors | cancelled | input | cached | output | reasoning | cache hit | cost USD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| primary | 4 | 1 | 1 | 2200 | 1500 | 180 | 40 | 0.6818 | 0.006700 |
| compact | 1 | 0 | 0 | 4000 | 1000 | 300 | 0 | 0.25 | 0.006000 |
| delegate | 1 | 0 | 0 | 300 | 100 | 25 | 0 | 0.3333 | 0.000500 |
| learning | 1 | 0 | 0 | 500 | 0 | 40 | 0 | 0.0 | 0.000800 |
| router | 1 | 0 | 0 | 80 | 0 | 2 | 0 | 0.0 | 0.000100 |

Run totals for the same ledger: `calls=8, errors=1, input_tokens=7080,
output_tokens=547, cached_input_tokens=2600, reasoning_tokens=40,
cost_usd=0.0141, cache_hit_ratio=0.3672`. The rows sum to every one of those
figures, so the reconciliation delta is

```
{'calls': 0, 'errors': 0, 'cancelled': 0, 'input_tokens': 0,
 'output_tokens': 0, 'cached_input_tokens': 0, 'reasoning_tokens': 0,
 'cost_usd': 0.0, 'ok': True}
```

**Guards.** `tests/test_issue58_purpose_totals.py` (15 tests, stdlib-only
`FakeDB` duck-typing `add_llm_calls` / `add_tool_calls`) asserts the per-purpose
rows against hand-written expectations, that `sum(by_purpose.calls)` and every
token sum equal `usage_totals`, that each row's `cache_hit_ratio` and the run's
agree, that a second `flush()` writes **zero** duplicate rows (no double
counting across the finalize + post-capture flushes), that a run with no calls
reconciles to zero rows instead of crashing, and that both seams
(`_fold_accounting`, `bench.purpose_breakdown`) carry the breakdown.

**Verification.**

```
cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \
  python3 -m pytest tests/test_issue58_purpose_totals.py -p no:cacheprovider -o asyncio_mode=strict

============================== 15 passed in 2.55s ==============================
```

**What this makes impossible.** A "win" that moves calls out of `primary` into
retries, compaction, router, learning or delegate calls now shows up as a
purpose row instead of vanishing, and any call that somehow reaches the ledger
without a purpose still shows up under `other`. If a future change records a
call in a way the breakdown cannot see, `reconcile(run_id)["ok"]` goes False and
the test fails — the totals are no longer just a number, they are a sum of named
parts.
