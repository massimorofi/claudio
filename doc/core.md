# claudio-core

The framework-free half of Claudio: storage, configuration, tools, safety,
context management and the wire protocol — everything about *what the system
does*, nothing about *how a model is driven*.

The defining property is that this package imports **no agent framework**. No
OpenAI Agents SDK, no Google ADK, no LangChain, no LiteLLM. That is not a style
preference. It is what makes the engine swappable, what makes this package
testable with only its own dependencies installed, and what lets the same core
serve a FastAPI backend today and something else tomorrow. The rule is enforced
mechanically by an import-linter contract rather than by convention, so a
violation fails the build instead of eroding over time.

- **Package:** `claudio_core`
- **Python:** ≥ 3.11
- **Runtime deps:** `pydantic`, `pyyaml`, `aiosqlite`, `httpx`, `croniter`, `pypdf`
- **Tests:** 134 passing
- **Contract:** `lint-imports` → *core is framework-free* **KEPT**

---

## Capabilities

### 1. Event protocol — `events.py`

The frozen SSE wire contract. Thirteen events, a `TypedDict` payload per event,
validation, and rendering.

| Event | Payload | Emitted when |
|---|---|---|
| `run_started` | `run_id, session_id` | a run begins |
| `token` | `text` | model output chunk |
| `reasoning` | `text` | model reasoning/thinking chunk |
| `tool_call` | `call_id, name, arguments` *(JSON string)* | model invokes a tool |
| `tool_result` | `call_id, name, output, is_error` | tool returns |
| `handoff` | `from_agent, to_agent` | control moves between agents |
| `approval_required` | `approval_id, tool, arguments, reason` | run paused for a human |
| `run_completed` | `run_id, session_id, final_output, usage, summary` | run finished |
| `run_failed` | `run_id, error{code, message, retryable}` | run terminated |
| `run_restarting` | `reason, attempt` | retry or fallback engaged |
| `run_compacted` | `reason, attempt` | context compacted (`manual`/`threshold`/`overflow`) |
| `context_usage` | `used_tokens, context_window, usable_tokens, free_tokens, percent_used, compact_threshold, source, auto_disabled` | context gauge update |
| `heartbeat` | — | keepalive |

Two rules are enforced in `encode_event()` rather than trusted to producers,
because both cause silent, confusing UI failures:

1. `tool_call.arguments` and `approval_required.arguments` must be **JSON
   strings**. If an object reaches React it is rendered as a child and the view
   crashes.
2. `tool_result.output` must be a **string**, for the same reason.

```python
from claudio_core import encode_event, format_sse

encode_event("tool_call", {"call_id": "c1", "name": "read_file",
                           "arguments": {"path": "x"}})
# → {"event": "tool_call", "data": {..., "arguments": '{"path": "x"}'}}

format_sse("token", {"text": "hi"}, seq=7)
# 'id: 7\nevent: token\ndata: {"text": "hi"}\n\n'
```

Unknown event names and contract-violating payloads raise `ValueError` at the
seam, so a producer bug surfaces loudly instead of as a blank screen.

### 2. Engine contract — `engine.py`

The seam between core and whatever drives the model. Core defines the interface;
the backend supplies an implementation.

```python
@runtime_checkable
class RunEngine(Protocol):
    async def stream(self, request: RunRequest) -> AsyncIterator[dict]: ...
    async def resume(self, request: RunRequest, state: Any) -> AsyncIterator[dict]: ...
    async def cancel(self, run_id: str) -> None: ...
```

Supporting types:

- **`ModelSpec`** — how to reach the inference endpoint: `model`, `base_url`,
  `api_key`, `temperature`, `max_tokens`, `timeout_seconds`, `fallback_model`.
  A plain description, not a client object: core must not construct or hold one.
- **`ToolPolicy`** — `enabled`, `disabled`, `approval_mode`, `strict_mode`.
- **`RunRequest`** — `run_id`, `session_id`, `workspace`, `instructions`,
  `user_input`, `model`, `tools`, `max_turns`, `history`, `metadata`.
  `instructions` arrives fully assembled from `instructions.py`; the engine must
  not add to it, so the prompt is identical whichever engine is in play.
- **`RunOutcome`** — `final_output`, `input_tokens`, `output_tokens`,
  `cost_usd`, `turns_used`, `raw`.
- **`EngineError(code, message, retryable)`** — maps directly onto `run_failed`,
  so the recovery loop can decide what to do without inspecting
  engine-specific exception types.
- **`is_terminal(event)`** — True for `run_completed` / `run_failed`.

An engine yields `{"event": name, "data": {...}}` dicts and emits
`run_completed` exactly once. It must **not** emit `run_started` — the
RunManager owns that, because it also owns the run's persistence.

### 3. Persistence — `db.py`

SQLite over `aiosqlite`. `Database(path: Path)` — note it requires a
`pathlib.Path`, not a string. `connect()` runs the schema script plus
incremental migrations.

| Table | Purpose |
|---|---|
| `claudio_sessions` | session metadata, running totals, `last_context_tokens`, `learning_captured_entries` (self-improvement watermark) |
| `claudio_config_history` | immutable config versions |
| `claudio_approvals` | approval requests and decisions |
| `claudio_runs` | run records, usage, final output |
| `claudio_health_checks` | daemon diagnostic results |
| `claudio_issues` | detected problems, status tracking |
| `claudio_suggestions` | proposed fixes attached to issues |
| `claudio_audit` | append-only audit trail |
| `claudio_tasks` | user-defined scheduled tasks |
| `claudio_task_runs` | per-execution task history |

Representative methods:

- **Sessions** — `create_session`, `get_session`, `list_sessions`,
  `update_session`, `delete_session`, `clear_all_sessions`,
  `list_compaction_disabled_session_ids`
- **Config** — `add_config_version`, `list_config_history`, `get_config_version`
- **Approvals** — `create_approval`, `get_approval`, `list_pending_approvals`,
  `resolve_approval`
- **Runs** — `create_run`, `update_run`, `get_run`, `list_runs`, `run_totals`,
  `fail_interrupted_runs`
- **Health / issues / suggestions** — `add_health_check`,
  `get_latest_health_check`, `list_health_checks`, `create_issue`, `get_issue`,
  `update_issue_status`, `count_open_issues`, `find_open_issue_by_title`,
  `add_suggestion`, `get_suggestion`, `list_suggestions_for_issue`,
  `update_suggestion`
- **Tasks** — `create_task`, `get_task`, `list_tasks`, `list_enabled_tasks`,
  `update_task`, `delete_task`, `add_task_run`, `update_task_run`,
  `get_task_run`
- **Audit** — `add_audit`, `list_audit`, `delete_audit_older_than`

`utcnow()` is exported for consistent timestamping.

### 4. Configuration — `settings.py`

Pydantic models over YAML, with environment overrides. Sections: `server`,
`llm`, `agent`, `mcp`, `monitoring`, `autonomous`. Top-level model is
`AppConfig`.

Any value can be overridden by environment variable using the `CLAUDIO__`
prefix and `__` as the path separator:

```bash
CLAUDIO__LLM__MODEL="gpt-5"
CLAUDIO__SERVER__AUTH_TOKEN="..."
```

Key functions:

| Function | Purpose |
|---|---|
| `load_config(path)` / `load_config_dict(path)` | load + validate |
| `validate_candidate(candidate)` | validate a proposed config without saving |
| `redact_secrets(cfg)` / `restore_redacted_secrets(candidate, current)` | secrets never round-trip through the UI |
| `changed_keys(old, new)` | diff two configs |
| `restart_required_for(changed)` | which subsystems need restarting |
| `write_config_atomic(path, cfg)` | temp-file + rename |
| `config_schema_json()` | JSON schema for the settings UI |

Restart semantics: `server.host` and `server.port` require a backend restart;
`mcp` requires an agent restart. Everything else is hot-reloadable.

Notable config fields:

- `LLMConfig` — `provider`, `model`, `api_key`, `base_url`, `temperature`,
  `max_tokens`, `fallback_model`, `timeout_seconds`, `retry_attempts`,
  `price_input_per_mtok`, `price_output_per_mtok`
- `AgentConfig` — `max_turns`, `max_budget_usd`, `approval_mode`,
  `workspace_dir`, `context_window`, `compaction_reserve`,
  `compaction_timeout_seconds`, `max_output_chars`, `strict_tools`,
  `skills_enabled`, `skills_dir`, `tools`, `subagents`
- `AutonomousConfig` — `enabled`, `report_only`, `health_check`, `maintenance`,
  `recovery`, `notification`, `command_allowlist`
- `SelfImprovementConfig` — `enabled` (master switch, off by default),
  `capture_enabled`, `consolidation_schedule` (cron, default weekly Mon 04:00),
  `promotion_threshold` (recurrence ≥ N promotes, default 3), `auto_promote`
  (apply skill changes without approval — trusted hosts only), `max_learnings`
  (episodic store cap, default 5000), `retention_days` (prune raw after 180),
  `max_auto_skills` (bloat guard, default 20). The `consolidation_schedule` is
  validated with `croniter` on load.

### 5. Tools — `tools/`

Ten built-in tools, each a plain `async` function whose first positional
argument is a `ToolContext`.

| Tool | Read-only | Destructive | Signature |
|---|---|---|---|
| `read_file` | ✅ | | `(ctx, path, start_line=None, end_line=None)` |
| `write_file` | | ✅ | `(ctx, path, content, append=False)` |
| `edit_file` | | ✅ | `(ctx, path, old_string, new_string, replace_all=False)` |
| `list_dir` | ✅ | | `(ctx, path=".")` |
| `rewind_files` | | ✅ | `(ctx, path=None)` |
| `run_command` | | ✅ | `(ctx, command, timeout=60.0)` |
| `web_fetch` | ✅ | | `(ctx, url, max_chars=None)` |
| `web_search` | ✅ | | `(ctx, query, max_results=5)` |
| `todo_write` | ✅ | | `(ctx, items: list[dict])` |
| `create_scheduled_task` | | | `(ctx, name, schedule, prompt='', script='', description='')` |

`tools/registry.py` holds the Claudio-specific semantics a framework does not
model:

- `TOOL_META` — `ToolMeta(name, is_read_only, is_destructive, path_arg)` per tool
- `TOOL_FUNCTIONS` — name → callable
- `needs_approval_for(meta, approval_mode)` — **static** bool decision
- `select_tool_names(enabled, disabled)` — resolve the effective tool list
- `tool_inventory()` — for `GET /agent/tools`

Why a static bool rather than a callable policy: a callable invites the framework
to inspect arguments and prove they were unchanged after validation. When a
model omits an optional parameter — very common with local models — that proof
fails and the framework demands approval for *every* call, including read-only
ones like `web_fetch`. A static bool skips the inspection entirely.
Out-of-workspace access is enforced inside the file tools themselves, not here.

`write_file` and `edit_file` back up the previous version before mutating, which
is what `rewind_files` restores from.

### 6. Safety — `safety/`

**`allowlist.py`** — the command safety model.

- `DEFAULT_ALLOWLIST` — the shipped safe-command set
- `effective_allowlist(config)` — configured list, falling back to default
- `is_allowed(command, config)` — normalised membership test. Shell
  metacharacters (`; | & $ > <` and newlines) are rejected outright, so an
  allowlisted command cannot be chained into something else.
- `is_catastrophic(command)` — pattern blocklist (`rm -rf /`, `mkfs`,
  `dd of=/dev/`, fork bombs and friends), checked before any allowlist logic
- `validate_command_entry` / `validate_service_name` / `validate_vacuum_size` —
  input validation for config and API
- `sudoers_snippet(config)` — generates the sudoers block for daemon deployment

**`guardrails.py`** — content screening.

- `screen_input(text) -> GuardrailResult` — prompt-injection pattern detection
- `screen_output(text) -> GuardrailResult` — output leakage screening
- `redact_secrets(text)` — API keys, tokens, passwords replaced in place
- `screen_tool_arguments(...)` — pre-execution argument screening

### 7. Context recovery — `recovery.py`

The "prevent, don't react" layer. Rather than catching a context-overflow error
after the model rejects the request, the outgoing request is sanitised first.

- `make_input_filter(max_context_tokens, max_write_chars, on_budget_exceeded,
  calibrator, truncation_tally)` → `sanitize(data: CallModelData) -> ModelInputData`
- `estimate_input_tokens(items, calibrator=None)` — character-based estimate
- `TokenCalibrator` — starts at `CHARS_PER_TOKEN = 2.4` and corrects itself
  against real reported token counts
- `TruncationTally` — counts repeated truncations; three in a row signals the
  agent is stuck and should abort rather than keep degrading
- `format_tool_error(args: ToolErrorFormatterArgs)` — turn a tool failure into a
  message the model can act on, including a closest-name suggestion for an
  unknown tool

Constants: `CHARS_PER_TOKEN`, `FIXED_REQUEST_OVERHEAD_TOKENS`,
`IMAGE_TOKEN_ESTIMATE = 1500`, `MIN_KEEP_ITEMS = 12`.

Truncation preserves `call_id`/`name` pairing so a truncated tool call stays
structurally valid; malformed arguments are replaced with `{}` rather than cut
mid-JSON.

### 8. Compaction — `compaction.py`

Summarises a long transcript into a compact block.

`Compactor(context_window, reserve, max_compact_tokens=2000, timeout_seconds=300.0)`

| Method | Purpose |
|---|---|
| `should_compact(input_tokens)` | threshold test |
| `is_disabled(session_id)` / `disable` / `enable` | per-session opt-out |
| `record_failure` / `record_success` | circuit-breaker bookkeeping |
| `build_summary_request(items)` | the summarisation prompt |
| `summarize(...)` | one summarisation call |
| `compact(...)` | full compact-and-replace |

`Summariser = Callable[[str], Awaitable[str]]` is **injected**. Core does not
know which client performs the call; `claudio_core.llm.openai_chat_completion`
is the default the backend supplies.

Circuit breaker: after `CIRCUIT_BREAKER_THRESHOLD = 3` failures, compaction is
disabled for that session. Compaction gets its own 300 s timeout, separate from
`llm.timeout_seconds`, because it is a single long generation — a ~12k-char
transcript summarised in one request. On a slow local reasoning model the
generic 120 s timeout is too short, and a timeout there silently kills
compaction for the whole session.

`render_items(items)` flattens a transcript to text.

### 9. Prompt assembly — `instructions.py`

`build_instructions(config, session_id, turns_since_todo=0, skill_entries=None,
reads_since_write=0, deliverable_written=False) -> str`

Assembles, in order: base identity → operating procedure (delivery contract) →
environment block (workspace, platform, date, approval mode) → skills catalog →
memory files → conditional nudges.

The two nudges are corrections for observed failure modes:

- **Write-first nudge** — fires at `READS_SINCE_WRITE_NAG = 8` read/lookup
  calls with no file produced. This is the "read forever" spiral; the reminder
  tells the agent to write a first draft now. Suppressed once any file exists,
  and worded so a genuinely read-only task can ignore it.
- **Todo nag** — fires after enough turns without a `todo_write` call, so the
  progress the user sees matches the work actually done.

### 10. Skills — `skill_loader.py`

Drop-in Agent Skills: each skill is a folder containing a `SKILL.md` with YAML
frontmatter.

- `scan_skills(skills_dir)` → `SkillScan` — discovers and validates, reporting
  accepted and rejected entries with reasons
- `build_skill_functions(scan)` → `dict` — callable skill helpers, plain Python,
  no framework wrapper
- `render_skill_block(entries)` — the catalog section of the system prompt
  (names and descriptions only; full text loads on demand)

Guards: `MAX_SKILL_FILE_BYTES` and `MAX_RESOURCE_BYTES` (100 KB each),
`MAX_FRONTMATTER_BYTES` (8 KB), kebab-case name validation, allowlisted
frontmatter keys.

### 11. Conversation store — `session_store.py`

A JSONL transcript mirror, independent of the database, so a session survives a
DB wipe and can be inspected with ordinary text tools.

- `ConversationStore` — `Protocol` (`get_items`, `add_items`, `clear_session`)
- `SessionStore(data_dir)` — `append`, `read`, `clear`, `delete`,
  `repair_orphans`

Storage is `{data_dir}/sessions/{session_id}.jsonl`, one entry per line.
`repair_orphans` drops tool calls with no matching result and vice versa, which
is exactly what a crash mid-turn leaves behind.

### 12. File extraction — `extract.py`, `file_context.py`

`extract_text(path, max_chars=30_000) -> ExtractResult` handles text, images,
`.docx`, `.xlsx` and `.pdf`, with binary sniffing and per-type extraction.

`compose_message_with_files(...)` builds the user message with attached file
contents, capped by `MAX_TOTAL_FILE_CHARS = 24_000` and
`MAX_PER_FILE_CHARS = 12_000`.

### 13. Memory files — `memory.py`

Discovers project instruction files — `CLAUDE.md`, `AGENTS.md`,
`.claude/CLAUDE.md` — in the workspace and config directory, and renders them
into a prompt block. Capped at 32 KB per file, 64 KB total.

### 14. Observability — `metrics.py`, `engine_metrics.py`, `logging_setup.py`

- `Metrics` — counters (requests, runs started/completed/failed, retries, tool
  errors, LLM errors), usage totals, LLM latency with percentiles, and
  `snapshot(runs_active, queue_depth)`
- `EngineMetrics(base_url)` — scrapes the inference server's Prometheus
  endpoint and derives KV-cache hit rate, prefill/decode throughput and queue
  depth. `metrics_url()` and `parse_prometheus()` are exported separately.
- `setup_logging(level)`, `JsonFormatter`, `RingBufferHandler`,
  `get_log_buffer()` — JSON logs plus an in-memory ring buffer for the Monitor
  view, with `sk-` secret scrubbing.

### 15. Direct LLM calls — `llm.py`

```python
openai_chat_completion(*, base_url, model, messages, api_key="local",
                       temperature=0.2, max_tokens=2048, timeout=300.0) -> str
```

A minimal OpenAI-compatible client over `httpx`. It exists so one-shot jobs —
compaction summarisation, a "test connection" ping — do not have to go through
the agent engine. It is deliberately *not* a provider abstraction: it speaks the
chat-completions wire format and nothing else. Raises `LlmCallError`.

`_join()` handles `base_url` with or without `/v1`, and with a full
`/chat/completions` suffix. `_extract_text()` handles both string and
block-list `content`.

### 16. Self-improvement capture — `learning.py`

The capture phase of the self-improvement loop: after a run completes, read
its transcript and ask the model to pull out reusable lessons — the three
markers the proposal names: **Error Encountered / Root Cause / Validated
Fix** — plus a category and a criticality flag. Each lesson becomes one
episodic row in `claudio_learnings`; the periodic consolidation pass (in
the backend) later distills recurring ones into skill drafts.

Like `llm.py`, this module is framework-free: extraction goes through
`openai_chat_completion` and carries no agent-framework dependency.

| Function | Purpose |
|---|---|
| `Learning` | dataclass: `category`, `title`, `error_description`, `root_cause`, `fix_applied`, `validated`, `critical` |
| `render_digest(transcript)` | flatten a transcript to a compact action/error digest (user + assistant + tool calls/results; reasoning deliberately excluded — verbose, larger injection surface, no error signal) |
| `extract_learnings(transcript, config)` | one LLM pass → `list[Learning]`; returns `[]` on nothing-found or call failure |
| `dedupe_or_insert(db, learning, …)` | FTS-search for a similar existing learning; bump its `recurrence_count` if found, else insert a fresh row |
| `capture_run(db, transcript, config, …)` | fire-and-forget entry point; swallows all errors so capture never breaks the run |

**Safety.** The transcript contains *untrusted* content (web pages, files,
command output). The extractor treats it strictly as data to summarize,
never as instructions to follow — the extraction prompt says so explicitly.
The downstream promotion gate (operator approval before a distilled skill
reaches the system prompt) is the real defense.

> **Capture is per-run-slice, not whole-transcript.** The backend's
> `_schedule_capture` hands this function only the transcript entries
> appended since the last capture (tracked by the session's
> `learning_captured_entries` watermark), not the entire cumulative
> transcript. Re-reading the whole history each run would re-extract the
> same fixed incident every time and inflate its `recurrence_count` past the
> promotion threshold on phantom repeats. See the backend's self-improvement
> section for the watermark mechanics.

---

## File structure

```
claudio-core/
├── pyproject.toml                  deps + import-linter contract
├── .venv/                          dev virtualenv
├── src/claudio_core/
│   ├── __init__.py                 public surface (see below)
│   ├── events.py                   ★ frozen SSE wire contract
│   ├── engine.py                   ★ RunEngine protocol
│   ├── db.py                       SQLite layer, 10 tables
│   ├── settings.py                 AppConfig, YAML + env overrides
│   ├── context.py                  ToolContext, context_usage_payload()
│   ├── llm.py                      framework-free chat-completion client
│   ├── learning.py                 self-improvement capture (episodic learnings)
│   ├── instructions.py             system-prompt assembly
│   ├── skill_loader.py             skill discovery / rendering
│   ├── memory.py                   CLAUDE.md / AGENTS.md discovery
│   ├── extract.py                  docx / xlsx / pdf / text extraction
│   ├── file_context.py             attach files to a user message
│   ├── recovery.py                 input filter, token calibration
│   ├── compaction.py               compaction + circuit breaker
│   ├── metrics.py                  counters and latency percentiles
│   ├── engine_metrics.py           Prometheus scrape (LM Studio)
│   ├── logging_setup.py            JSON logging + ring buffer
│   ├── safety/
│   │   ├── __init__.py             re-exports allowlist + guardrails
│   │   ├── allowlist.py            command allowlist, catastrophic patterns
│   │   └── guardrails.py           injection screening, secret redaction
│   └── tools/
│       ├── registry.py             TOOL_META, TOOL_FUNCTIONS, policy
│       ├── files.py                read / write / edit / list / rewind
│       ├── shell.py                run_command
│       ├── web.py                  web_fetch, web_search
│       ├── planning.py             todo_write
│       └── tasks.py                create_scheduled_task
└── tests/
    ├── test_event_protocol.py      wire contract + TypeScript mirror
    ├── test_allowlist.py
    ├── test_engine_metrics.py
    ├── test_files_core.py
    ├── test_input_filter.py
    ├── test_settings.py
    └── test_skill_loader.py
```

---

## Public API

Everything below is re-exported from the package root. Other functions live in
their own modules and are imported from there — notably
`claudio_core.settings.config_schema_json`, which is not part of the root
surface.

| Group | Symbols |
|---|---|
| Meta | `__version__` |
| Context | `ToolContext`, `context_usage_payload` |
| Persistence | `Database`, `utcnow`, `SessionStore`, `ConversationStore` |
| Config | `AppConfig` |
| Engine contract | `RunEngine`, `RunRequest`, `RunOutcome`, `ModelSpec`, `ToolPolicy`, `EngineError`, `is_terminal` |
| Events | `EventName`, `ALL_EVENT_NAMES`, `encode_event`, `format_sse` |
| Prompt | `build_instructions`, `discover_memory_files`, `render_memory_block` |
| LLM | `openai_chat_completion`, `LlmCallError` |
| Skills | `Skill`, `SkillScan`, `scan_skills` |
| Tools | `ToolMeta`, `TOOL_META`, `ALL_TOOL_NAMES`, `needs_approval_for`, `select_tool_names`, `tool_inventory` |

Subpackages are importable directly when the root export is not enough:

```python
from claudio_core.compaction import Compactor
from claudio_core.recovery import make_input_filter, TokenCalibrator
from claudio_core.safety import is_allowed, is_catastrophic, screen_input
from claudio_core.session_store import SessionStore
from claudio_core.learning import capture_run, extract_learnings, Learning
```

### `ToolContext`

Passed as the first argument to every tool. Carries the immutable configuration
plus the mutable per-run accumulators the tools update.

| Field | Notes |
|---|---|
| `config` | the `AppConfig` |
| `session_id` | owning session |
| `workspace` | resolved workspace root |
| `approval_mode` | `off` / `on_request` / `always` |
| `db` | `Database` instance |
| `run_id` | set at run start |
| `cost_usd` | running cost |
| `turns_since_todo` | drives the todo nag |
| `deliverable_written` | flips once a file is produced |
| `reads_since_write` | drives the write-first nudge |
| `agent_factory` | reserved for subagent construction |

Property `max_output_chars`; methods `resolve_path(path)` and
`is_within_workspace(path)`.

---

## Development

```bash
cd claudio-core
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m pytest -q    # 134 passed
.venv/bin/lint-imports           # core is framework-free KEPT
```

`lint-imports` is the one that matters. It fails if any module under
`claudio_core` imports `agents`, `google`, `openai`, `litellm`, `langgraph` or
`semantic_kernel`. Run it before committing.
