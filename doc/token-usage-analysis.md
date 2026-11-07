# Cutting Claudio's token usage without losing intelligence

Measured on the live run that produced the dsh-superagent analysis (run `6dae3f0d`, session
`1f908a92`, 2026-11-05 20:55→21:39 UTC). Every figure is read from the session transcript,
`claudio.db`, `config/config.yaml` or the engine source. Nothing here is a guess from the README.

## Headline

| | |
|---|---|
| **input_tokens** | **3,118,010** |
| **output_tokens** | **29,190** → ratio **107 : 1** |
| wall clock | 2,627 s (44 min) |
| tool calls | 71 (`run_command` 39, `edit_file` 14, `write_file` 11, `todo_write` 4, 3 others) |
| model calls | **55–72** (ambiguous only because parallel calls share a turn) |
| unique content actually produced | **264,505 chars** |
| …re-sent across calls | **11.1 M chars → 42× amplification** |
| recorded cost | **$0.00** (`price_input_per_mtok: 0`) — so nothing in the UI signals this |

**One line: 264 KB of conversation cost 3.1 M input tokens because every model call re-pays for
all of history. Output is 1% of the bill — talking less is not the lever. Fewer calls, smaller
retained context, and cache visibility are.**

## 1. Anatomy of what was retained (measured)

| What | chars | % of context | note |
|---|---|---|---|
| tool results | 162,910 | 62% | median result 648, but **9 results pinned at the 8,000-char cap** — the tail dominates |
| tool-call arguments | 63,725 | 24% | **75% of it is `write_file`+`edit_file` payload** — document text that also lives on disk |
| reasoning summaries | 34,883 | 13% | 54 events, median 407 chars |
| final assistant answer | 2,666 | 1% | |
| user turns | 321 | 0% | |

Per-call fixed cost, rendered from the live config: **system prompt 12,637 chars ≈ 3,160
tokens**, of which the **skills catalogue is 8,196 chars — 65% of the entire system prompt**
for 21 skill one-liners. Plus tool schemas (10 enabled built-ins ≈ 3.4 KB of docstrings, MCP
declarations on top).

## 2. The mechanism, and a model you can check

Each model call sends: system prompt + tool schemas + the whole conversation so far. History
grows roughly linearly, so total input grows **quadratically** in the number of calls:

```
total_input = Σ_i ( fixed + history_i )     history_i ≈ i × (content per call)
```

Fitting that to this run reproduces reality to within 2.5%: predicted **3,196,509** vs
**measured 3,118,010**. Two readings fit (72 calls at 3.91 chars/token, or 55 calls at 3.22 —
both normal for a Qwen tokenizer on markdown/code/JSON), and both agree on the essentials:
**average context 43–57 k tokens per call, final context 68–82 k tokens, re-send factor 35–42×.**

Because the model is calibrated against the measured total, the savings below are ratios from a
validated model rather than hand-waving. Simulator: `doc/token_usage_sim.py`, replays this transcript.

**Nothing was compacted.** `context_window: 262144`, `compaction_reserve: 8192` → the compaction
threshold is 254 k tokens and this run peaked at ~68–82 k. Compaction never came close. So the
"it's fine, compaction handles it" answer is wrong for runs of exactly this length — and this is
the typical length for a real task.

## 3. Ranked fixes — simulated on this transcript

| # | Change | Simulated saving | Intelligence risk |
|---|---|---|---|
| 1 | **Evict stale tool results**: >12 steps old → 120-char digest | **49%** | Low — see §4 |
| 2 | Same at >6 steps | **56%** | Low–medium |
| 3 | + drop `reasoning` parts from replay | **58%** | Low |
| 4 | + cap tool output 8,000 → 4,000 chars | **67%** | Medium (fix the *shape*, not just the cap: §5.3) |
| 5 | + 72 → 45 model calls | **78%** | **None — this is the free one** |
| 6 | All of the above | **80%** (3.12 M → ~0.64 M) | Low |

Item 5 is free because it costs no information: it is purely about how the work is batched.

## 4. Why eviction does not cost intelligence

The worry is "if I delete old tool output, the agent forgets". It does not, for three
structural reasons, all observable in this very run:

1. **The conclusions survive.** By step 40, what mattered from step 5's `git clone` output has
   already been re-expressed in later reasoning, in `edit_file` diffs and in the deliverable.
   What did *not* survive is the byte-exact 8 KB listing — which is exactly what costs 42×.
2. **It is re-fetchable.** 39 of 71 calls were `run_command` and 25 were writes/edits to files
   that are still on disk. A digest line (`ran: git clone … → 1,204 chars, ok`) plus the command
   itself lets the agent re-derive anything it turns out to need. Lossy compression with a
   re-read path is not the same as loss.
3. **The window already forces this choice.** A 264 k window with a 42× re-send factor is not
   "unlimited"; it just moves the failure from *expensive* to *catastrophic*: a run twice this
   long hits compaction, which rewrites the whole history with one summary — strictly more
   destructive than evicting old tool payloads at step boundaries.

Rules that keep it safe, in priority order:

- **Never evict**: the last 12 steps, any `is_error: true` result, anything referenced by a
  still-open todo, and the deliverable's own text.
- **Digest, don't delete**: keep the first line + char count + `ok/error` + the command/path so
  it stays re-fetchable.
- **Evict payload, not provenance**: the `tool_call` (what was asked) stays; the 8 KB blob goes.
  This is why `write_file`/`edit_file` args (75% of all call arguments here) are the *best*
  candidate: the content is on disk and `git`-recoverable, yet ~48 KB of document text sat in
  every one of the remaining calls.

**Where to put it:** `engine/reasoning_llm.py:144` `ReasoningOpenAILlm._generate_content_streaming`
already wraps every request — no new architecture. Apply a retention transform to `contents`
there (pure function, unit-testable), driven by two new settings, e.g.
`agent.tool_result_ttl_calls: 12` and `agent.tool_result_digest_chars: 160`.

## 5. The free 20–30%: fewer calls, better-shaped results

Item 5 above (72 → 45 calls) needs no new machinery, and this run shows exactly where the calls
went. These are my own behaviour in this session, so they are also prompt/skill fixes:

**5.1 Batch shell work.** 39 `run_command` calls; a dozen of them were one-line `grep`/`wc`
checks that I could have run as one script. Each avoided call saves the *whole current context*
(43–57 k tokens), not the size of the command. Prompt rule worth adding: *"before a read-only
command, ask whether two more checks can go in the same shell invocation."*

**5.2 Parallel calls did not reduce steps.** 71 calls landed on 71 distinct turn boundaries even
where I issued two in one turn. Worth verifying in `runs/manager.py` whether parallel tool calls
are truly one model turn or serialized — if serialized, the instructions' advice to batch is
half-true and the fix is in the engine.

**5.3 Fix the *shape* of the 8,000-char cap, not just the number.** The one `web_fetch` in this
run cost 8,068 chars, and the majority of it was GitHub's navigation chrome — "Sign in",
"Pricing", "Universe", cookie management — because `web_fetch` keeps the **first** N characters.
The article text was truncated away. A middle-truncation plus boilerplate strip (nav/footer/
cookie blocks) would have returned the same *information* in ~1/3 the tokens, and the fix also
helps `run_command` (`head -100` on a `find` is the same mistake). Rule of thumb the system
prompt should carry: **the cap should keep the informative part, not the first part.**

**5.4 Write the deliverable in fewer, larger edits.** 11 `write_file` + 14 `edit_file` = 25 of
71 calls, 47.8 k chars of permanent history. Draft-then-edit is good craft; but a document
written top-to-bottom in 4 appends instead of 11 chunks, with one consolidated verification pass
instead of 14 micro-edits, is the same craft at half the calls.

**5.5 The skills catalogue is the cheapest fixed win.** 8,196 of 12,637 system-prompt chars are
21 skill one-liners, re-sent every call ≈ 147 k tokens per run. The catalogue is worth having
(discoverability = intelligence), so don't cut it — **filter it**: include only skills whose
category matches the task, or collapse to names-only for skills never loaded in this session.
Halving it is ~70 k tokens/run for free.

## 6. The finding that outranks everything above

I probed the inference server (`192.168.1.172:18080`) directly:

```json
"usage": { "prompt_tokens": 59, "completion_tokens": 8,
           "prompt_tokens_details": { "cached_tokens": 0 },
           "prompt_tokens_per_second": 298.3,
           "completion_tokens_per_second": 68.1,
           "gufo": { "component_cache": { "reuse": "none",
                     "deepest_ram_tokens": 0, "deepest_disk_tokens": 0,
                     "selected_tokens": 0, "restored_bytes": 0,
                     "prefilled_tokens": 59 } } }
/props → { "total_slots": 1, "n_ctx": 262144 }   llama.cpp 0.24.0
```

Three consequences, in order of importance:

**6.1 `cached_tokens` is right there and Claudio throws it away.**
`engine/adk_engine.py:309 usage_from_event()` returns a 2-tuple `(input, output)` and reads only
`prompt_token_count`/`input_tokens`. So the `claudio_runs` table (columns: `input_tokens`,
`output_tokens`, `cost_usd`) records 3.1 M as if it were all fresh compute. **We literally cannot
tell cache hits from misses** — the one number that separates "healthy long run" from "wasteful
long run". Fix is ~5 lines: read `prompt_tokens_details.cached_tokens` (plus the `gufo` block if
present) and add `cached_tokens` to the table, the `context_usage` event and the monitoring view.

**6.2 Most of those 3.1 M tokens were probably *already* cached — so aim at hit-rate, then at
count.** 29,190 output tokens at 68 tok/s ≈ 429 s of the 2,627 s run. If 3.1 M prompt tokens had
to be freshly prefilled even at a generous 298 tok/s, that is ~10,400 s — four times the whole
run. So prefix reuse is carrying most of the load, and the honest reading is:

> Raw token reduction shrinks the *uncached remainder*, not the total. Expect the wall-clock
> benefit of §3 to be smaller than 80% — and **measure `cached_tokens` before and after, or you
> will tune blind.** The count still matters for reasons 6.3 and 6.4.

**6.3 `total_slots: 1` makes the cache fragile, and it is invisible to us.** One KV slot means any
*other* traffic — an autonomous health check, a cron task running its own agent, a second chat
session, a subagent — evicts the interactive run's cache. Every subsequent call then re-prefills
the full history instead of one step. This is the most likely cause of a run that "suddenly"
costs 3–5× more, and today it leaves no trace. Mitigations, cheapest first: give llama.cpp more
slots; stop the autonomous daemon from running agent tasks while an interactive run is active
(`RunManager.active_count()` already knows); and record a `cache_hit_ratio` per run so eviction
storms become visible instead of mysterious.

**6.4 `n_ctx` matches (`262144`), so no silent truncation** — one thing that is *not* wrong.

## 7. What not to cut (this is the "same intelligence" part)

| Temptation | Why it costs intelligence here |
|---|---|
| Shorten the delivery contract (`OPERATING_PROCEDURE`, ~2.4 KB) | It is what stops the read-forever-with-no-deliverable failure; `instructions.py:34` says so explicitly. Honest note from this run: with the contract present, the first `write_file` still landed at **turn 9 of 71** — so it is doing real work (the file appeared early-ish and never stopped growing) but it is not a magic bullet, and removing it would almost certainly make the spiral worse, not better. |
| Remove search-hygiene rules | Prevents re-querying a CAPTCHA'd provider forever — historically a much bigger waste than its size. |
| Cut the skills catalogue entirely | Discoverability *is* capability; 21 one-liners are what makes the right procedure load. Filter (§5.5), don't delete. |
| Lower `max_output_chars` to 2,000 flat | This run needed the 8 KB outputs — the repo trees, the `wc` tables, the fetched README. Blunt truncation destroys signal and causes re-reads, which cost a whole call each. Fix the *shape* (§5.3) instead. |
| Aggressive compaction (lower the threshold) | Compaction rewrites history wholesale into one summary. Evicting old tool payloads is strictly gentler and reversible; get to compaction later, not sooner. |
| Reduce reasoning (`thinking`) budget | Reasoning is 13% of context and 100% of the quality on analysis work like this. Drop it from *replay* (§3 item 3), not from the model. |
| Chase `cost_usd` | Both prices are 0, so every run reports `$0.00` and the budget guard is inert. Until prices are set, tokens ≠ money and the `max_budget_usd: 100` circuit breaker protects nothing. |

## 8. Do-this-order

1. **Measure first** (~½ day): capture `cached_tokens` (+ `gufo` reuse), add `cache_hit_ratio`
   per run, expose it in `/monitoring/runs`. Without this, steps 2–5 are unfalsifiable.
2. **Serialize cache contention** (~1 h): don't start autonomous agent tasks while
   `active_count() > 0`; note `total_slots: 1` in the docs.
3. **Tool-result retention transform** in `ReasoningOpenAILlm._generate_content_streaming`
   (TTL 12 steps, digest 160 chars, never evict errors / last 12 / deliverable). Largest single
   win: ~49–58%.
4. **Elide written-file payloads** from history once the write succeeded (`write_file` +
   `edit_file` = 75% of call arguments here, and the content is on disk).
5. **Result shaping**: middle-truncation + boilerplate strip in `web_fetch`; prefer head+tail over
   head-only in `run_command`.
6. **Prompt hygiene**: batch read-only shell work; write deliverables in fewer larger appends.
7. **Skills catalogue filtering** by task category.
8. Give the budget breaker a real unit. Prices are 0 because the model is a local GGUF — per-token
   money is meaningless here. Budget on **prefill seconds** (`prompt_tokens / prompt_tokens_per_second`,
   both already returned by the server) or on uncached tokens; that is what actually consumes the box.

Expected: **~60–80% fewer input tokens**, with the wall-clock share depending on cache hit-rate —
which step 1 will finally show.

## 9. Method, evidence, and what I could not measure

**How each number was obtained.** Run totals and `input_tokens`/`output_tokens`/duration from
`claudio.db → claudio_runs` and the `turn_status` record in the session transcript
(`/srv/claudio/claudio-backend/data/sessions/1f908a92….jsonl`, filtered to `run_id 6dae3f0d` —
the file also contains the run that is answering this question, which contaminates unfiltered
counts). Content anatomy from that transcript's `tool_result` / `tool_call` / `reasoning`
records. System prompt re-rendered live from `build_static_instructions()` with the real
`config/config.yaml` and 21 scanned skills. Savings from `doc/token_usage_sim.py`, which replays this
transcript under each policy; **the simulator reproduces the measured 3,118,010 within 2.5%**,
which is what licenses the percentages in §3. Server facts from live `curl` to `/props`,
`/v1/models` and one `/v1/chat/completions` probe.

**Caveats, stated plainly.**

- **Model-call count is ambiguous (55–72)** because the transcript stamps each tool call
  individually, so parallel calls are indistinguishable from sequential ones. Both readings are
  internally consistent (3.91 vs 3.22 chars/token, both plausible). It changes the average
  context per call, not the ranking of fixes. Resolving it is part of step 1.
- **The chars/token calibration is fitted, not derived** — there is no tokenizer in this
  container, so chars/token is solved from the known total. That makes absolute token figures
  self-consistent by construction; the *relative* savings are what should be trusted.
- **§3 savings are model output, not measured reality.** They assume no cache effects. Given §6.2
  the wall-clock win will be smaller; the token-count win should be close.
- **Unverified:** whether ADK replays `thought` parts back to the model (item 3's premise). The
  transcript stores reasoning separately and `adk_engine.py:283` only maps `thought` to the UI
  event, so this needs a one-request experiment before claiming the 9%.
- **Unverified:** whether parallel tool calls become one model turn or several (§5.2).
- Not examined: whether `spawn_subagent` is in use (it is enabled, unused in this run) — subagent
  fan-out is a known cost tail and would deserve its own analysis.

**Deliverable:** `doc/token-usage-analysis.md`. Simulator: `doc/token_usage_sim.py`.
