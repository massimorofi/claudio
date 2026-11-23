# Performance Improvement Plan — status (issue #58)

Plan: reduce per-turn latency and token waste without changing what Claudio can
do. Each increment is gated by a test that measures the claim, not by a
refactor that looks right.

| Increment | Scope | Status | Proof |
| --- | --- | --- | --- |
| 0 | Unified accounting: one ledger for every model call, tool call and lifecycle counter | **done** | `tests/test_issue58_accounting.py` |
| 0b | Telemetry aggregates: prompt-cache hit ratio, tool payload weight, **concurrency overlap ratio** | **done** | `tests/test_issue58_telemetry.py` |
| 1 | Bounded parallel read execution + deterministic `ToolContext` reducer | **done** | `tests/test_issue58_concurrency.py`, `tests/test_issue58_telemetry.py` |
| 2 | Read fast paths (`batch_read`, `search_with_context`, `locate_symbol`) | done | `tests/test_issue58_fastpaths.py` |
| 3 | Tier-1 context compaction | done | `tests/test_issue58_tier1.py` |
| 4 | Streaming / first-output latency | done | `tests/test_issue58_streaming.py` |
| 5 | Routing and budgets | done | `tests/test_issue58_router.py`, `tests/test_issue58_budgets.py` |

## Increment 1 — done

Increment 1: **done**. Bounded parallel reads, the deterministic `ToolContext`
reducer, cancellation propagation and the telemetry that proves the overlap are
all merged and covered by `tests/test_issue58_concurrency.py` and
`tests/test_issue58_telemetry.py`.

What landed:

**Bounded parallel reads.** `claudio_core.tool_batch.run_tool_batch` walks a
turn's tool calls in request order. Consecutive read-only calls form a *wave*
that runs concurrently under an `asyncio.Semaphore`; the bound is
`agent.max_read_concurrency` (default 4, hard ceiling 8,
`HARD_MAX_READ_CONCURRENCY`) and is read back from the run's
`RunConcurrency.max_read_concurrency` so the number reported is the number
enforced. A mutating, exclusive or approval-gated call closes the wave and runs
alone under `mutation_lock` / `exclusive_lock`, so a read can never observe a
half-applied write that preceded it.

**Mutating tools are excluded by construction.** `classify_tool` is the only
door to the parallel path and it returns `read_only` only for registry entries
that are read-only, non-destructive and not approval-gated under the run's
`approval_mode`. `run_parallel_reads_only` raises `NonParallelToolError` rather
than downgrading. Asserted by `test_mutating_tools_are_excluded_from_the_parallel_path`,
`test_mutations_in_a_mixed_batch_never_overlap` and
`test_read_only_registry_entries_are_actually_side_effect_free`.

**Order is the model's, not the scheduler's.** Results are keyed by the original
request index and returned sorted by it, whatever order they completed in
(`test_results_come_back_in_original_request_order`).

**Deterministic turn-boundary reducer.** `reduce_tool_context` folds a batch
into conversation state: sorted by request index, deduplicated on
(tool, argument digest), size-capped by an explicit character budget (overflow
is listed in `dropped_indices`, never silently lost), and cancellation-aware
(aborted calls become compact status markers, not missing entries). No timings,
randomness or unordered containers reach the output, so two runs with the same
inputs produce **byte-identical** context — asserted across two real runs and
across permuted inputs. `apply_tool_context` writes the canonical block plus its
SHA-256 into `ToolContext.tool_context_text` / `tool_context_digest`.

**Cancellation.** A cooperative `asyncio.Event` aborts in-flight reads (their
coroutines are cancelled, not awaited to completion) and stops not-yet-started
ones from starting; completed results survive and reduce to the same bytes as
always (`test_cancellation_aborts_inflight_reads_and_keeps_completed`,
`test_cancellation_prevents_unstarted_reads_from_running`).

**Telemetry.** Every batch call is recorded in the Increment 0 ledger with a
shared, deterministic `concurrency_group` (`read:<batch-id>` derived from the
requests). `accounting.overlap_ratio` = `sum(duration) / span`; a serial
sequence scores ~1.0, a four-read batch measured through the ledger scores
**> 1** (`test_read_batch_overlap_ratio_exceeds_one`). `make_adk_tool` now
stamps its start time *inside* the guard so recorded durations are execution
time, not queue time — otherwise the ratio would be inflated by waiting.

## Notes / remaining work

* The ADK path already fans a turn's calls out concurrently, and `make_adk_tool`
  enforces the same semaphore/locks, so production runs are safe today.
  `run_tool_batch` is the framework-free scheduler the tests (and any non-ADK
  caller) drive; folding the engine's turn loop onto it — so a production batch
  carries one `concurrency_group` per turn instead of the run-wide one — is the
  next step.
* The reducer produces the canonical block; wiring it into the outbound prompt
  (replacing the raw tool-response splice) is the next step, so the byte-stable
  block is what the provider actually sees and prompt-cache hits stop depending
  on completion order.
* `batch_read`'s clip marker can overshoot the shared budget by a few dozen
  characters (pre-existing `_clip` behaviour); harmless, left alone deliberately.
* Child issue update via `gh` was skipped in this environment: the `gh` CLI is
  not installed and no GitHub credentials are present.
