# Claudio Specs — Reverse-Engineered Claude Code Architecture

**Target analyzed:** Claude Code CLI v2.1.296 (native ELF binary, embedded minified JS)
**Evidence sources:** string-literal extraction from the binary, live session transcripts (`~/.claude/projects/*/*.jsonl`), file-history store, and observable runtime behavior.
**Confidence labels:** *CONFIRMED* = direct string/structure evidence; *INFERRED* = reasonable interpretation of partial evidence.

---

## 1. Overview

Claude Code is a terminal agent that wraps the Claude Messages API in a **recursive agentic loop**: the model decides what to do by calling tools; the harness executes those tools locally and feeds the results back; the cycle repeats until the model stops calling tools. Everything else — compaction, permissions, hooks, file history, subagents — is scaffolding around that loop.

Three pillars:

1. **The loop** — stream → parse → run tools → append results → recurse.
2. **Context management** — token accounting, auto-compaction, persistent JSONL transcripts.
3. **Recovery** — retries with backoff, model fallback, orphan repair, reactive compaction, file rewind.

---

## 2. The Agentic Loop

### 2.1 Shape (CONFIRMED)

The loop is a **recursive async generator** (minified `Qt`/`query`), not a while-loop. Each iteration:

```
loop(messages, turnCount):
    response = stream_api_call(messages)          # SSE stream
    blocks   = collect_blocks(response)           # thinking | text | tool_use
    messages.append(assistant(blocks))

    switch map_stop_reason(response.stop_reason):
        "stop":      return done
        "resume":   continue without user input    # pause_turn / compaction
        "run_tools":
            results = execute_tools(blocks)       # see §2.3
            messages.append(user(tool_results))
            return loop(messages, turnCount + 1)  # transition: {reason:"next_turn"}
```

### 2.2 Stop-reason state machine (CONFIRMED)

Exact mapping found in the binary:

| API `stop_reason` | Mapped action |
|---|---|
| `tool_use` | `run_tools` |
| `pause_turn`, `compaction` | `resume` (continue without user input) |
| `end_turn`, `stop_sequence`, `max_tokens`, `model_context_window_exceeded`, `refusal` | `stop` |
| `null` | `stop` |

Edge cases handled explicitly:
- `stop_reason === "tool_use"` but **zero tool blocks emitted** → recovery path (the model claimed tools it didn't emit).
- Synthetic assistant frames (harness-generated, e.g. injected reminders) set `stop_reason = toolBlocks.length > 0 ? "tool_use" : "end_turn"`.

### 2.3 Tool dispatch (CONFIRMED)

- Each tool exposes: `isEnabled`, `isConcurrencySafe`, `isReadOnly`, `isDestructive`, `inputSchema`, `outputSchema`, `renderToolUseMessage`, `coerceInput`, `toAutoClassifierInput`.
- Tool_use blocks are **grouped into batches** by `isConcurrencySafe()`: safe blocks run **in parallel**, an unsafe block breaks the batch and runs **serially**.
- Parallelism cap: `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY ?? 10`.
- Results are `tool_result` blocks keyed by `tool_use_id`. Failures are returned **to the model as content**, not thrown:
  `is_error: true` + text wrapped in `<tool_use_error>…</tool_use_error>`.
  Known error shapes: `InputValidationError: …`, `No such tool available: …`, `Cancelled: Claude ended the conversation`, `Streaming fallback - tool execution discarded`.
- Structured remedy channel: failures can carry `toolResultRemedy` (e.g. kind `memory_paused`) — a machine-readable "do this instead".

### 2.4 Built-in tools (CONFIRMED by string evidence)

`Bash`, `Read`, `Write`, `Edit`, `MultiEdit`, `Glob`, `Grep`, `Task`/`Agent`, `WebFetch`, `WebSearch`, `TodoWrite`, `Skill`, `AskUserQuestion`, `NotebookEdit`, `ExitPlanMode`, `BashOutput`, `KillShell`/`KillBash`, `SendUserMessage`, `PushNotification`, `ScheduleWakeup`, `ListMcpResources`, `ReadMcpResource`, plus the newer task-system family `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList`, `TaskStop` (which augments/replaces TodoWrite), and the Artifact family (`Artifact`, `ArtifactData`, `ArtifactComments`).

### 2.5 Turn limits and budgets (CONFIRMED)

- `max_turns`: positive int, max 200, **default 10** (agent execution config).
- On hit: yields `{type: "max_turns_reached", maxTurns, turnCount}`; result subtype `error_max_turns`, text `Reached maximum number of turns (N)`.
- Per-agent overrides: worker agents `maxTurns: 500`, utility agents `maxTurns: 15`, single-shot utility forks (compact, rename, away-summary) `maxTurns: 1`.
- **USD budget**: `maxBudgetUsd` → `error_max_budget_usd` ("Exceeded USD budget").
- Stop-hook blocking is capped: `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP ?? 8` (a Stop hook can't block forever).

### 2.6 Streaming (CONFIRMED)

- SSE (`text/event-stream`, `anthropic-version: 2023-06-01`).
- Events: `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`.
- Delta types: `text_delta`, `input_json_delta`, `thinking_delta`, `signature_delta`, `citations_delta`, `compaction_delta`.
- Tool input JSON is accumulated from `input_json_delta.partial_json` and parsed at `content_block_stop`.
- Robustness telemetry: `stop_reason_received`, `ms_to_headers`, `stalls_over_30s`; "Stream ended cleanly mid-response … treating as a dropped connection"; a **streaming-fallback** path that discards partial tool execution and re-requests non-streamed.

---

## 3. System Prompt Assembly (CONFIRMED)

- Base identity: *"You are Claude Code, Anthropic's official CLI for Claude."* Variants: `"...running within the Claude Agent SDK"`; coordinator variant: *"an AI assistant that orchestrates software engineering tasks across multiple workers"*.
- Injected context fields: `userEmail`, `attachedProject`, `currentDate`, `gitStatus`, `perforceMode`, `cacheBreaker`, `workerToolsContext`, `Environment`, `auto memory`, `Memory`, `Scratchpad Directory`.
- Environment block: primary working directory, is-git-repo flag, additional working dirs, platform, shell, OS version.
- **CLAUDE.md discovery order:** `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `.claude/CLAUDE.md`, plus `~/.claude/CLAUDE.md` and `~/.claude/rules/*`. Size-capped ("skipping … exceeds N byte limit"); external imports gated by "Allow external CLAUDE.md file imports?".
- `git status` is prefetched (`gitStatusPrefetch`) and can be omitted (`omitGitStatus`).
- Dynamic injections ride in `<system-reminder>` tags with explicit framing: *"Tags contain information from the system. They bear no direct relationship to the specific tool results or user messages in which they appear."*

---

## 4. Long & Complex Task Handling

### 4.1 Planning scaffolding (CONFIRMED)

- **TodoWrite nag:** constants `{TURNS_SINCE_WRITE: 10, TURNS_BETWEEN_REMINDERS: 10}`. The harness scans history for the last `TodoWrite` call and last reminder; when both thresholds pass it injects a reminder ("…consider using the TodoWrite tool to track progress…"). Disableable (`off`).
- **Plan mode:** `ExitPlanMode` tool; `plan_mode_exit` attachment; plan-mode reminders with `TURNS_BETWEEN_ATTACHMENTS`. On resume, the saved plan renders under "## Plan (from TodoWrite state)".

### 4.2 Subagents (CONFIRMED)

- Subagent messages are written with `isSidechain: true` and their own `agentId`; main-loop loaders filter them out (`!isSidechain`).
- A subagent gets a **fresh context** (own system prompt, no parent conversation). Its intermediate work never enters the parent's context — **only its final report text** returns as a tool result.
- `fork_briefing` entries carry inherited context for forked agents; `subagentFrameResult` handles hook-rewritten outputs.
- This is the primary mechanism for "read across many files without polluting the main context."

### 4.3 Background work (CONFIRMED)

- Long-running shells: `BashOutput` (poll), `KillShell`/`KillBash`.
- `Monitor` (stream events), `CronCreate`/`CronDelete`/`CronList` (scheduled prompts), `ScheduleWakeup` (dynamic-loop pacing).
- Background agent status survives compaction (it's part of what the post-compact window keeps, §5.3).

---

## 5. Memory & Context Management

### 5.1 Session persistence — JSONL transcript (CONFIRMED)

Location: `~/.claude/projects/<project-slug>/<session-uuid>.jsonl`, append-only.

Entry types observed:

| Type | Purpose |
|---|---|
| `user` | user message (with `promptId`, `origin`, `turnPosition`, cwd, gitBranch) |
| `assistant` | assistant message (with `stop_reason`, content blocks, `apiBlockIndex`, `requestedModel`) |
| `system` | system notices |
| `attachment` | injected context: `environment`, `model`, `date`, `skill_listing`, `agent_listing_delta`, `credential_org`, `prompt_snapshot`, `session_context`, `remote_session_change` |
| `permission-mode` / `mode` / `atis-latch` | session state latches |
| `last-prompt` | `{lastPrompt, leafUuid}` — resume pointer |
| `ai-title` | auto-generated session title |
| `file-history-snapshot` | `{messageId, snapshot: {trackedFileBackups, timestamp}}` |
| `file-history-delta` | `{snapshotMessageId, trackingPath, backup: {version, backupTime, …}}` |
| summary entries | carry `isCompactSummary: true` |

Every entry links via `parentUuid` → the transcript is a **tree** (branchable; `leafUuid` marks the live head).

Resume flags: `--resume <id>`, `--continue`, `-r`, `--fork-session`, `--resume-session-at`, `--resume-drops-turn`, `--from-pr`.

### 5.2 Token accounting (CONFIRMED)

- Primary: API endpoint `/v1/messages/count_tokens` (maxRetries: 1), with per-provider fallbacks (first-party/Bedrock/Vertex).
- Local estimate fallback: **4 bytes per token** (`wFe = 4`), per-model via `bytesPerTokenForModel`.
- Live tracking combines estimate + API usage fields (`input_tokens`, `cache_read_input_tokens`).

### 5.3 Auto-compaction (CONFIRMED)

**Threshold math:**
```
effective_window = context_window − min(max_output_tokens, cap)   # default window 200000; 1M via [1m] suffix
compact_at     = effective_window − 13000    # VMt = 13000 (buffer reserved for the summary itself)
blocked_at     = effective_window − 3000     # urr  = 3000
warn_at        = effective_window − 20000
```

**Tunables:** `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `DISABLE_AUTO_COMPACT`, `DISABLE_COMPACT` (also kills manual `/compact`), `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`, `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE`; settings `autoCompactEnabled` (default true), `idleCompaction`.

**Precompute ("arm"):** default fraction 0.2 — background summary generation starts at 20% before the threshold (`tengu_precomputed_compact_started`), so the summary is ready when needed. *INFERRED.*

**Circuit breakers:** 3 consecutive compaction failures trips a breaker ("autocompact: circuit breaker tripped"); a separate **rapid-refill breaker** detects context refilling to the limit within 3 turns of the previous compact, 3 times in a row ("Autocompact is thrashing…").

**Summary prompt variants** (registry `{control, lean, short, short_v2, capped}`):
- **control** — 9-section structured summary: 1. Primary Request and Intent, 2. Key Technical Concepts, 3. Files and Code Sections, 4. Errors and fixes, 5. Problem Solving, 6. **All user messages** (list ALL non-tool-result user messages), 7. Pending Tasks, 8. Current Work, 9. Optional Next Step. Honors user-supplied "## Compact Instructions" / "# Summary instructions" found in context.
- **lean** — "Your reply replaces everything above: the next context window opens with what you write inside `<summary></summary>` tags… Nothing else survives, including any earlier summary."
- **short / short_v2** — preserve: difficulties/problems, options considered, decisions (exact wording), current state, open/unresolved items, hard-to-reconstruct specifics (names, numbers, dates, links). "Do not call any tools while writing this summary." v2: if the transcript begins with an earlier summary, copy the user-request record unchanged and rewrite only the work account.
- **capped** — "Summarize the entire conversation above in 2000 words or fewer."
- **partial** — summarizes only recent messages when earlier context is retained.
- Failure path: "Failed to generate conversation summary - response did not contain valid text content."

**What survives compaction** (injected as a user message with `isCompactSummary: true`):
> "This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation."

Plus: system prompt + CLAUDE.md/memory files (unchanged), **up to 5 recently-read files re-read from disk** (long ones truncated), the plan, loaded skills, and background agent/shell status. `PostCompact` hooks run after. `repeatsCompactSummary` flag exists for re-injection.

**Microcompact:** clears old tool results **without summarizing** (`toolsCleared`, `toolsKept`, `keepRecent`, `tokensSaved`; trigger `context_hint`). Cheaper than full compaction.

**Manual:** `/compact` (same machinery; `compact_boundary` system event marks it), `/clear` (drops conversation).

### 5.4 File history / rewind (CONFIRMED)

- Store: `~/.claude/file-history/<session-uuid>/<content-hash>@vN` — versioned backup copies (v1, v2, …) of every tracked file the agent edits.
- Transcript snapshot/delta entries reference `trackedFileBackups` per message.
- `/rewind` — rolls back the turn's **tool-made** file edits by replaying these backups. Explicit caveat: *"Rewinding does not affect files edited manually or via bash."* CLI flag `--rewind-files`; `canRewind` checks availability. Rename/copy failures are logged so a rewind reports missing files rather than silently corrupting.

---

## 6. Error Handling & Recovery

### 6.1 API retries (CONFIRMED)

- Exponential backoff with jitter: `min(500 × 2^(attempt−1), 30000) + random×500` ms (500ms base, doubling, 30s cap).
- `retry-after` header honored on **429 and 503**; also `anthropic-ratelimit-unified-slow-retry-after`.
- Retryable statuses: `408, 409, 429, ≥500`. 529 = `overloaded_error` ("Upstream at capacity; client backs off and retries").
- Error taxonomy: `{rate_limit, overloaded, server_error}`.
- Counters: `maxRetries`, `maxSleptThrough`, `maxOverloaded`, `initialConsecutive529Errors`, `consecutiveFailures`.
- Tunables: `CLAUDE_CODE_OVERLOADED_RETRY_BASE_DELAY_MS`, `CLAUDE_CODE_OVERLOADED_RETRY_MAX_DELAY_MS`, `CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES`.
- UI: `Retrying in Xs · attempt N/M`.

### 6.2 Model fallback (CONFIRMED)

- `fallbackModel` config + `FallbackTriggeredError` (fields: `originalModel`, `fallbackModel`, `reason`).
- Trigger enum: `model_not_found`, `permission_denied`, `overloaded`, `server_error`, `last_resort` (non-retryable error on primary), `model_blocked` (per-model kill switch).
- Decision: retry overloaded up to `maxOverloaded`; beyond that, if a fallback exists → `useFallbackModel`.
- **Turn-scoped:** "Switched to X because Y is not available" — the primary is re-tried on the next user turn.

### 6.3 Tool-level errors (CONFIRMED)

Every failure becomes a `tool_result` with `is_error: true` wrapped in `<tool_use_error>`. The model sees the failure as content and adapts — this is the loop's fundamental recovery primitive: **tools never crash the loop; they report and let the model retry differently.**

### 6.4 Permission denials (CONFIRMED)

- Denial is also returned as tool content: "Permission for this tool use was denied. The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file). Try a different approach…"
- `toolDenialKind` values: `user-rejected`, `interrupted`, `cancelled`, `denied_by_session_rule`, `denied_by_auto_mode`, `automode-unavailable`, `hook_blocked`, `denied_by_rule`.
- Built-in safety denials are explicitly distinguished from user denials ("…denied by a built-in Claude Code safety check, not by the user").
- Auto-mode classifier outage is surfaced: "…temporarily unavailable (timed out), so auto mode cannot determine the safety of Bash right now…".

### 6.5 Hooks (CONFIRMED)

- Events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `UserPromptSubmit`, `UserPromptExpansion`, `Stop`, `SubagentStop`, `SessionStart`, `SessionEnd`, `Notification`, `PreCompact`, `PostCompact`, `TaskCreated`, `TaskCompleted`, `PermissionRequest`.
- Exit-code semantics (verbatim): **exit 2 = "block"** — the guarded action (tool call, permission request, prompt) is blocked. Ignored for async hooks and on Stop/SubagentStop. Other non-zero = "Failed with non-blocking status code" → treated as non-blocking.
- JSON output: `PreToolUse` → `hookSpecificOutput.permissionDecision` (`allow`/`deny`/`ask`); `PostToolUse` → `additionalContext`.
- **Rewake:** async hooks can wake the model on exit 2 (`asyncRewake`, `rewakeMessage`) — a background check that failed later can re-enter the loop.
- Transcript records `hook_blocking_error` / `hook_additional_context` message types.

### 6.6 Context overflow recovery (CONFIRMED)

- Detection: regex `/prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)/i` (parses actual vs limit), plus `"input is too long for requested model"`.
- Recovery = **reactive compaction** (`reactive_compact`), with `hasAttemptedReactiveCompact` and `reactiveCompactDeeperPass` — a round ladder of progressively deeper compactions; last resort keeps trailing messages with head truncation.
- `max_tokens` truncation: overflow adjustment with guard "max_tokens overflow adjustment made no progress"; prompt-too-long parsed to recompute the effective window.
- Image overflow handled separately ("Image was too large. Double press esc…").

### 6.7 Interrupt / crash recovery (CONFIRMED)

- ESC aborts in-flight work (`[inProcessRunner] … current work aborted (Escape pressed)`).
- Markers injected into history: `[Request interrupted by user]`, `[Request interrupted by user for tool use]`, `[Tool call did not complete: the turn was ended to deliver the message …]`; `interrupted_message_id` records the cancelled API message id.
- **Orphan repair:** `ensureToolResultPairing` — on load/resume, any `tool_use` block missing its `tool_result` gets a synthesized error result (`is_error: true`, `toolDenialKind: "interrupted" | "cancelled"`). This keeps the API's strict tool_use↔tool_result pairing invariant intact after any crash.
- Orphaned permission prompts are restored on resume (`handleOrphanedPermission`, mode `orphaned-permission`, `restoredOrphans`, `handedOffTurn`).

### 6.8 Auth errors (CONFIRMED)

- 401 → try token refresh first ("refresh token stored — reconnecting and retrying once"), then user-facing "Invalid API key · Please run /login" / "Your session has expired…".
- Server contract: `x-should-retry: false` on revoked/expired bearer so the prompt is immediate.
- Billing: "Credit balance too low · Add funds: …". Key format validation client-side.

---

## 7. Design Principles Extracted

1. **The loop is simple; the scaffolding is deep.** One recursive generator + stop-reason switch. All sophistication lives in what surrounds it.
2. **Errors are data, not exceptions.** Tool failures, permission denials, even interrupts become `is_error` tool_results the model can reason about. The loop almost never dies from a failed action.
3. **Context is the scarce resource.** Everything (subagent isolation, microcompact, the 13k summary buffer, the 5-file re-read, todo nagging) exists to spend tokens well.
4. **Write-ahead persistence.** Every message, file backup, and state latch is appended to the JSONL transcript as it happens, so any crash is recoverable by replay + orphan repair.
5. **Defense in depth on runaway behavior:** turn caps, USD budgets, stop-hook block caps, compaction circuit breakers, rapid-refill thrash detection.
6. **Recovery is layered:** retry → fallback model → reactive compact → deeper compact → last-resort truncation. Each layer fires only when the previous one is exhausted.
7. **Isolation for scale:** subagents keep their own context and return only conclusions; the parent stays lean.

---

## 8. Minimal Implementation Checklist (for Claudio)

- [ ] Recursive async loop with stop-reason switch (§2.1–2.2)
- [ ] Tool registry with `isConcurrencySafe` batching + per-tool schemas (§2.3)
- [ ] `tool_result` wrapping for all failures, including validation (§2.3, §6.3)
- [ ] SSE streaming with partial-JSON accumulation and stall detection (§2.6)
- [ ] Append-only JSONL transcript with `parentUuid` chain + `last-prompt` pointer (§5.1)
- [ ] Resume: load transcript, run `ensureToolResultPairing` (§6.7)
- [ ] Token counting: API `count_tokens` + 4 bytes/token fallback (§5.2)
- [ ] Auto-compact at `window − 13000`, with structured summary prompt and "what survives" contract (§5.3)
- [ ] Circuit breakers: 3 failed compactions, rapid-refill thrash (§5.3)
- [ ] Retry/backoff: `min(500·2^n, 30s) + jitter`, honor `retry-after` (§6.1)
- [ ] Fallback model on non-retryable errors, turn-scoped (§6.2)
- [ ] File-history backups per edit + `/rewind` (§5.4)
- [ ] Todo/planning scaffolding with turn-based reminders (§4.1)
- [ ] Subagent spawning with fresh context, report-only return (§4.2)
- [ ] Hook system with exit-code semantics (§6.5)

---

## 9. Alternative Foundation Analysis: OpenAI Agents SDK

*Could the OpenAI Agents SDK serve as the base for an enhanced Claudio? Evaluated against the architecture documented above.*
*Sources: [OpenAI Agents guide](https://developers.openai.com/api/docs/guides/agents), [Agents SDK Python](https://openai.github.io/openai-agents-python/), [Agents SDK TypeScript](https://openai.github.io/openai-agents-js/).*

### 9.1 What the SDK provides

A thin, opinionated **agent-loop engine** ("control the agent loop in your application with reusable agents, tools, and handoffs"):

| Primitive | Function |
|---|---|
| `Agent` | instructions + tools + guardrails + handoffs + output type |
| `Runner` | the loop: turns, tool dispatch, handoff transitions, `MaxTurnsExceededError` |
| `RunContext` / `RunState` | typed per-run state, **resumable** after interruptions |
| Tools | `@function_tool` with automatic schema + Pydantic validation; hosted tools (`shellTool`, `applyPatchTool`, `webSearchTool`, `codeInterpreterTool`, `computerTool`, `toolSearchTool`) |
| Handoffs | first-class agent→agent delegation |
| Guardrails | 4 layers (input / output / tool-input / tool-output), run **in parallel** with the agent, tripwire errors |
| Sessions | `MemorySession`, `OpenAIConversationsSession` (server-side), `OpenAIResponsesCompactionSession` (server-side compaction), SQLAlchemy / Redis / Mongo / Encrypted / Dapr |
| HITL | `RunToolApprovalItem` — pause for approval, resume from `RunState` |
| Tracing | spans + exporters, wired into OpenAI's eval / fine-tuning suite |
| MCP | stdio / SSE / streamable-HTTP servers, plus hosted MCP |
| `SandboxAgent` | isolated workspace: `Manifest`, `Permissions`, `SnapshotSpec`; capabilities Filesystem, Shell, Memory, **Skills**, **Compaction** (Unix-local or Docker backends) |
| Multi-provider | `LiteLLMModel` / `AnyLLMModel` → Anthropic and other providers reachable |

### 9.2 Coverage against the Claude Code spec

**Well covered:** the loop itself (§2.1), retry/backoff (`ModelRetrySettings` — cf. §6.1), session persistence with stronger backends than CC's JSONL (§5.1), server-side compaction (§5.3), tool approval as a core primitive (§6.4), structured outputs via Pydantic/StandardSchema (stronger than CC), MCP support, and the `SandboxAgent` — which maps strikingly well onto CC's execution model (shell + filesystem + skills + compaction under a declarative permission manifest).

**Missing — must be hand-built regardless:** todo-nag scaffolding (§4.1), plan mode, the permission *rule engine* (allow/deny/ask rules + auto-mode classifier — the SDK ships raw approval items, not the policy layer), CLAUDE.md/memory discovery (§3), file-history rewind (§5.4), background shells/monitors/cron (§4.3), and the token-budget circuit breakers (§5.3). That is most of the harness depth that makes CC effective.

**Semantics that don't match:**
- **Handoff ≠ subagent.** OpenAI's handoff *transfers* the conversation to another agent; CC's Task tool *spawns a child and keeps the parent* (§4.2). Agents-as-tools partially covers the spawn pattern.
- **Hosted tools run on OpenAI's servers.** A local-first coding tool needs files, shell, and git on the *user's* machine; hosted `shellTool`/`fileSearchTool` are the wrong shape.
- **Server-side conversation state assumes the Responses API.** With non-OpenAI models the SDK falls back to Chat Completions semantics and loses server-side compaction/conversations advantages.

### 9.3 The decisive problem: provider grain

The SDK is OpenAI-first. Running **Claude** through it means a LiteLLM/Any-LLM translation layer, and you lose exactly the Claude-specific behaviors that make Claude Code good: prompt-cache control, extended-thinking fidelity in the stream, tool-use nuance, and stop-reason edge handling (§2.2). You would be building a Claude-powered product while fighting your foundation's assumptions. Conversely, the SDK's own strengths (hosted tools, server-side state, GPT-5 reasoning settings) are things a Claude-native harness should not want.

### 9.4 Verdict

**Technically yes, strategically no — for a Claude-powered Claudio.** The SDK supplies a solid loop, sessions, guardrails, HITL, and sandboxing, but it is optimized for OpenAI's platform surface, and ~70% of the harness that makes Claude Code feel good would still be hand-built, while paying a translation tax on the model that matters most.

**What to steal instead (provider-agnostic, where the SDK is genuinely ahead):**

1. **Typed run items** (`RunToolCallItem`, `RunReasoningItem`, …) instead of raw message dicts — far more debuggable loop and UI.
2. **Parallel guardrails** as a primitive distinct from hooks — input/output/tool-level safety that fails fast without blocking the loop.
3. **Resumable approval state** — pause mid-run for a human decision and resume exactly, as a first-class `RunState` operation.
4. **Sandbox manifest model** — declarative `Manifest` + `Permissions` + `SnapshotSpec` is a cleaner security posture than bash-on-host with prompt-level caution.
5. **Tracing → evals discipline** — every run traced as spans, feeding an eval set: the actual engine for *enhancing* an agent over time.

**Exception:** if Claudio should be **multi-model** (OpenAI + Anthropic + local models) and Claude is accepted as one interchangeable provider rather than the core of the experience, the SDK becomes a viable provider-agnostic orchestrator base.

---

## 10. Execution Plan / Workflow View (implemented)

*Status: **IMPLEMENTED** in Claudio. Detailed design in `claudio_specs_workflow.md`; this section records what actually shipped and how it maps onto the architecture documented above.*

The reverse-engineered spec (§4.1) treats the plan as invisible scaffolding — a `TodoWrite` nag and a plan-mode attachment. Claudio makes the turn's progress a **first-class visible surface**: four components that turn the loop's internal state into something the user can read at a glance, persisted so it survives reload.

### 10.1 The four surfaces

| Surface | Shows | Data source | Persisted |
|---|---|---|---|
| **Plan tracker** | The agent's *intent* — a live checklist with per-item status | `todo_write` tool result | session row `todos` JSON |
| **Workflow panel** | The turn's *record* — one numbered, status-bearing tool-call trace | `tool_call` / `tool_result` events | JSONL transcript (mirrored) |
| **Context gauge** | Used / free against the usable window, with the compact-threshold tick | `context_usage` event | session row `last_context_tokens` |
| **Completion card** | The turn's outcome — done / failed / interrupted + what was produced | `turn_status` entry | JSONL transcript |

The plan (intent) and the workflow (record) are deliberately distinct: the plan is what the agent *said it would do*, the workflow is what it *actually did*. Both render per turn; the plan pins at the top, the workflow collapses under the answer.

### 10.2 Backend additions

All additive; none break the existing event contract.

1. **`context_usage` SSE event** — emitted at run start (after the proactive `_maybe_compact`) and after each `response.completed`. Payload built by `_context_usage_payload(used, config, source, auto_disabled)`:
   ```
   usable = context_window − compaction_reserve
   free   = max(0, usable − used)
   percent = used / usable × 100
   ```
   `source` is `"model"` when the provider reported `input_tokens`, `"estimate"` (chars // 4) otherwise. `auto_disabled` reflects the compaction circuit breaker (§5.3).
2. **`turn_status` transcript entry** — appended at every terminal state by `_append_turn_status`: `completed` (files_written, commands_run, duration_seconds, cost_usd), `failed` (error_code, error_message, duration_seconds), `interrupted` (duration_seconds). This is the bookend that makes an action-only turn legible.
3. **Tool-event mirroring** — `_mirror_tool_event` appends each `tool_call` / `tool_result` to the JSONL transcript as it streams. `repair_orphans` (§6.7) already understands these entry types, so a run interrupted mid-tool gets a synthesized result on the next read. This is what lets the workflow panel reconstruct on reload.
4. **Produced-artifact tracking** — `_track_produced` accumulates `record.files_written` (from `write_file` / `edit_file` / `create_file`) and `record.commands_run` (from `run_command`) during the stream, surfaced on the completion card and in `run_completed.summary`.
5. **`POST /sessions/{id}/compact`** — manual compaction: `require_auth`; `404` if no session; `409` if a run is active (compacting mid-run races the run's appends); `503` if the compactor is unavailable; otherwise runs the shared `Compactor` instance, updates `last_context_tokens`, and returns the fresh `context_usage`. The circuit-breaker state is shared with the RunManager — no second compactor is constructed.
6. **Prompt support** — `BASE_IDENTITY` now instructs the agent to write a `todo_write` list at the start of any multi-step task, so the plan tracker has real content.

### 10.3 Frontend components

| File | Role |
|---|---|
| `WorkflowPanel.tsx` | Collapsible numbered tool-call trace; per-step expand shows full args + output; approvals render inline as steps |
| `PlanTracker.tsx` | Pinned checklist; ☐ pending / ◐ in-progress / ☑ done; `X/Y done` header; renders nothing when empty |
| `ContextGauge.tsx` | SVG donut; used arc + reserve band + threshold tick; color bands green/amber/red; click → confirm → compact |
| `CompletionStatus.tsx` | Done / failed / interrupted card with produced files + command count + duration + Retry |
| `workflow.ts` | `toolDetail()` (first 3 args as `k=v`) and `groupIntoTurns()` (segments the flat item list into turns) |

State lives in `chatState.ts`: `plan`, `context`, `lastUserText`, and a `turn_status` item kind. `useChat.loadSession` reconstructs all of it from the transcript by mapping each entry on its `type` (`user` / `assistant` / `tool_call` / `tool_result` / `turn_status`), matching tool results back to their call by `call_id`.

### 10.4 What the implementation corrected over the spec

The spec (§3.6, §7.2) assumed tool items were already mirrored to the JSONL transcript and that the transcript was "user + assistant entries only" — those two statements contradict each other, and the second was the actual state. The workflow panel's reload-reconstruction criterion (§3.7) could not be met without mirroring, so **tool-event mirroring was added** (item 3 above).

One spec feature was **dropped** after real use: the Phase 3 floating workflow chip (§5). In practice it occluded the chat it was meant to float over and duplicated the plan tracker without adding glanceable value, so it was removed. The four surfaces above are what shipped; the plan tracker is the single home for live progress, docked in the transcript rather than floating.

### 10.5 Design principles this adds

1. **Progress is observable, not inferred.** The plan and the workflow make the loop's internal state legible without reading prose.
2. **Intent and record are different objects.** `todo_write` (what was planned) and the tool trace (what was done) are tracked separately so drift between them is visible.
3. **Every turn ends explicitly.** The completion card guarantees a terminal marker regardless of whether the assistant produced text — "did it finish?" never requires guessing.
4. **Reload is a first-class path, not an afterthought.** Mirroring tool events to the transcript means the UI reconstructs the full turn from disk, consistent with the write-ahead-persistence principle (§7.4).
