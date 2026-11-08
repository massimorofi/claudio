# claudio-backend

The Google ADK deployable. It wires `claudio_core` to ADK and exposes the HTTP
API the frontend consumes.

The architectural point of this package is confinement. ADK is a dependency of
exactly one subpackage — `engine/`. Everything above it (HTTP routes, run
orchestration) talks to the framework-neutral `RunEngine` protocol defined in
core. A second import-linter contract enforces that, so replacing the engine
later is a change to one package rather than a hunt through the backend.

- **Package:** `claudio_backend`
- **Python:** ≥ 3.11
- **Deps:** `claudio-core`, `google-adk[openai]>=2.0,<3`, `fastapi`,
  `uvicorn`, `pydantic`, `pyyaml`, `aiosqlite`, `httpx`, `croniter`
- **Status:** complete and operational — engine, run orchestration and HTTP
  routes are all ported and serving traffic.

---


## Capabilities

### 1. ADK engine — `engine/adk_engine.py`

Implements `claudio_core.engine.RunEngine` over Google ADK 2.12. Takes a
framework-neutral `RunRequest`, drives one ADK agent run, and translates ADK
events into Claudio SSE events.

Three design decisions, each established by probing ADK directly rather than
from its documentation.

**Tool wrapping.** Every Claudio tool takes `ToolContext` as its first argument.
ADK builds its tool declaration from the callable's signature, so a bound `ctx`
would leak into the schema as a required model-facing parameter. Each tool is
wrapped in a `**kwargs` closure that is then given a synthetic `__signature__`
listing only the model-facing parameters. ADK reads `__signature__` when
building the declaration, so the schema comes out correct and `ctx` stays
invisible to the model.

Verified output for `write_file`:

```json
{"name": "core_write",
 "parameters_json_schema": {
   "properties": {"path": {...}, "content": {...}, "append": {...}},
   "required": ["path", "content", "append"],
   "title": "core_writeParams", "type": "object"}}
```

Note: `FunctionDeclaration` has **no `.required` attribute** in this version —
`required` lives inside `parameters_json_schema`.

**Approval and pause.** `require_confirmation=True` is inert unless the `App` is
built with `ResumabilityConfig(is_resumable=True)`. When it is, ADK emits three
events and stops:

1. the model's original `function_call` (id `c1`, name `write_file`), with
   `long_running_tool_ids: []`
2. an injected `adk_request_confirmation` call with its **own** id
   (`adk-<uuid>`), args `{"originalFunctionCall": {...}, "toolConfirmation":
   {"hint": "...", "confirmed": false}}`, and
   `long_running_tool_ids: ["adk-<uuid>"]` ← this is the pause signal
3. a `function_response` for the **original** id `c1` carrying
   `{"error": "This tool call requires confirmation..."}`, with
   `actions.requested_tool_confirmations = {"c1": {...}}` and
   `actions.skip_summarization = true`

Resuming requires a `function_response` keyed by the **confirmation call's** id,
not the original tool-call id:

```python
types.Content(role="user", parts=[types.Part(
    function_response=types.FunctionResponse(
        id=conf_id, name="adk_request_confirmation",
        response={"confirmed": True}))])
```

Using the original `c1` id produces **zero events** — a silent no-op. This is
the single most important thing in the file, and it is not documented anywhere in
ADK's own material.

**Cross-process resume verified.** A completely fresh Python process with a new
`SqliteSessionService`, new `FunctionTool`s, new `App` and new `Runner`
successfully resumed a run that a *different, already-exited* process had
paused. That property is what makes Claudio's "pause → HTTP approval → resume"
flow work on ADK: the approval round-trip can span a process restart.

**Text delivery.** ADK delivers text per-response, not token-by-token, so a
`token` event from this engine is a chunk of model output rather than a
streamed token. The contract permits that and the frontend appends.

#### Public symbols

| Symbol | Purpose |
|---|---|
| `AdkEngine` | the `RunEngine` implementation |
| `AdkEngine.stream(request)` | drive a run, yield SSE events |
| `AdkEngine.resume(request, state)` | continue a paused run |
| `AdkEngine.cancel(run_id)` | cooperative cancellation |
| `AdkPauseState` | the pause checkpoint: `confirmation_id`, `original_call_id`, `tool_name`, `arguments`, `hint`, `approved`, `rejection_reason` |
| `AdkPauseState.to_json()` / `.from_json()` / `.from_dict()` | round-trip through SQLite |
| `normalise_state(state)` | accept the pause state in any of its shapes |
| `make_adk_tool(name, ctx, require_confirmation=)` | build one wrapped `FunctionTool` |
| `translate_event(event)` | one ADK event → zero or more Claudio events |
| `find_confirmation(event)` | extract `AdkPauseState` if present |
| `usage_from_event(event)` | `(input_tokens, output_tokens)` |
| `adk_state_path(request)` | where ADK keeps its session DB |
| `CONFIRMATION_TOOL` | `"adk_request_confirmation"` |
| `create_engine(config, db, kind=None)` | factory; `kind` defaults to `$CLAUDIO_ENGINE`, then `"adk"` |

#### Event translation

ADK mixes function calls, function responses and text within a single event, so
`translate_event` fans out rather than mapping one-to-one.

| ADK | Claudio |
|---|---|
| `part.function_call` (not the confirmation tool) | `tool_call` |
| `part.function_response` | `tool_result` (error detected from `response.error` / `ok: false`) |
| `part.text` with `thought=True` | `reasoning` |
| `part.text` from a non-user author | `token` |
| `adk_request_confirmation` call | `approval_required` (handled by the driver, which also captures the pause state) |
| `event.usage_metadata` | `context_usage` |

The driver loop (`_drive`) additionally enforces `max_turns`, checks the
cancellation set on every event, accumulates usage and cost, converts any
unexpected exception into `run_failed`, and emits `run_completed` at the end.


### 2. Run orchestration — `runs/manager.py`

Ported and running. The `RunManager` owns everything that is not about the
model: run lifecycle, the SSE fan-out and reconnection machinery, budget
enforcement, the recovery loop, and finalisation.

For profile resolution, role assignments, automatic routing, delegation, and
the intended workload for each model, see
[Model profiles, roles, and routing](./archive/model-routing.md).

- **`RunRecord`** — per-run state: a ring buffer of `(seq, sse)` pairs, the
  subscriber queue set, a cancel event, the task handle, token/cost
  accumulators, `files_written`, `commands_run`, `turns_used`,
  `resume_state_json`, and the `ToolContext` mutable mirror
  (`turns_since_todo`, `deliverable_written`, `reads_since_write`,
  `last_context_tokens`, `mid_run_compactions`).
- **`start_run(run_id, session_id, message, approval_mode=None)`** — create
  the record, persist the run row, spawn the driver task.
- **`resume_run(run_id, session_id, state_json, approval_id, decision,
  rejection_message=None)`** — continue a run paused for approval.
- **`cancel_run(run_id) -> bool`**, `get_record(run_id)`,
  `get_active_run_for_session(session_id)`, `active_count()`.
- **`request_compact(session_id) -> dict`** — manual compaction.
- **`open_stream(run_id, after_seq=0)` / `close_stream(...)`** — the
  reconnection machinery: replay everything after `after_seq` from the ring
  buffer, then continue live, so a dropped connection loses nothing.
- **Budget gate** against the session's `total_cost_usd` before any work
  starts.
- **Recovery loop** — proactive compaction, mid-run compaction capped at
  `MAX_MID_RUN_COMPACTIONS = 5`, exponential-backoff retry up to
  `llm.retry_attempts`, then one fallback-model attempt.
- **`_finalize_completed`** — deliverable verification, todo reconciliation,
  session totals, the `run_completed` summary, and the self-improvement
  capture pass (see below).
- Exceptions `BudgetExceededError`, `ContextThresholdReached`,
  `DeliveryFailureError`.

The one genuinely non-mechanical piece of the port is **persisting
`ToolContext`'s mutable accumulators** (`cost_usd`, `turns_since_todo`,
`deliverable_written`, `reads_since_write`) to SQLite alongside the run
record and rebuilding them on resume, so a resumed run continues the counters
instead of resetting them.


### 3. HTTP API — `api/`

All routes are under `/api/v1`. All except `GET /healthz` require
`Authorization: Bearer <server.auth_token>`.

The full per-endpoint reference lives with the surface it belongs to rather than
being duplicated here:

| Area | Where it's documented |
|---|---|
| Chat / run control (stream, blocking, cancel, reconnect) | this section, below |
| Approvals (human-in-the-loop) | this section, below |
| Sessions (list/detail/compact/messages/clear) | this section, below |
| Config (read / merge-patch / validate / test / history / restore) | [configuration.md](./configuration.md#config-api) |
| Files & workspace (upload / tree / download) | [skills.md](./skills.md#workspace) |
| Agent management (start / stop / restart / status / tools / skills) | this section, below |
| Monitoring (health / metrics / logs / runs) | this section, below |
| Autonomous daemon (health / maintenance / recovery / issues / tasks) | this section, below + daemon modules |
| Learning & skill-drafts (self-improvement read API) | [core.md](./core.md#16-self-improvement-capture--learningspy) |

**Error envelope.** Every non-2xx response uses:

```json
{"error": {"code": "...", "message": "...", "retryable": false, "details": {}}}
```

Raised as `ApiError(status, code, message, retryable, details)`.

**Chat.** `POST /chat/stream` creates the session if missing, mirrors the user
message (with a `📎 <names>` suffix when files are attached), starts the run,
then streams replay + live events (`HEARTBEAT_SECONDS = 15`). `POST /chat` is
the blocking variant (waits on the run task with a `llm.timeout_seconds * 2`
timeout; raises **409** `approval_required` on a pause, **504** `timeout`).
`GET /chat/runs/{run_id}/stream?last_event_id=N` is the reconnection endpoint:
it replays everything after `last_event_id` from the ring buffer then continues
live, so a dropped connection loses nothing. `POST /chat/runs/{run_id}/cancel`
cancels a run; `GET /chat/runs/{run_id}` returns the run record.

**Approvals.** `GET /approvals/pending` lists open confirmations;
`POST /approvals/{approval_id}` with `{decision: "approve"|"deny", reason}`
resolves one and resumes the paused run from its persisted `resume_state_json`.
The endpoint is **idempotent** — a second click on an already-resolved approval
returns the stored decision with `already_resolved: true` rather than failing.

**Sessions.** `GET /sessions`, `GET /sessions/{id}` (detail plus
`context_usage` computed from stored `last_context_tokens`),
`POST /sessions/{id}/compact` (manual compaction),
`GET /sessions/{id}/messages?limit=200` (transcript; runs `repair_orphans`
first), `GET /sessions/{id}/active_run`, `DELETE /sessions/{id}`,
`POST /sessions/{id}/clear`.

**Agent management.** `POST /agent/start` (202), `POST /agent/stop` (graceful),
`POST /agent/restart` (202), `GET /agent/status`
(`{state, started_at, active_runs, uptime_seconds, config_generation}`),
`GET /agent/tools` (tool inventory from core), `GET /skills` (discovered skills).

**Monitoring.** `GET /monitoring/health`, `GET /monitoring/metrics`,
`GET /monitoring/logs`, `GET /monitoring/runs`.

**System.** `GET /healthz` (liveness, **no auth**), `GET /readyz` (readiness),
`GET /version` (backend + protocol version).

> Auth-token rotation, the merge-patch semantics of `PUT /config`, secret
> redaction/restore, and restart semantics are all documented in
> [configuration.md](./configuration.md).

## Autonomous daemon

The `autonomous/` package is the watch / maintain / repair daemon. It is always
scheduled but gates itself on `autonomous.enabled` each cycle, so toggling the
feature needs no restart. The modules:

| Module | Job |
|---|---|
| `service.py` | daemon orchestration |
| `scheduler.py` | cron scheduling for the built-in passes |
| `task_scheduler.py` | user-defined task scheduling |
| `task_runner.py` | task dispatch (HANDLERS) |
| `health.py` | health-check pass (disk/mem/swap/load/journal) |
| `maintenance.py` | maintenance playbook |
| `recovery.py` | boot-time service recovery |
| `fixer.py` | agent-proposed fix commands |
| `agent.py` / `tools.py` | autonomous agent helpers + daemon-side tools |
| `script.py` | operator-script runner |
| `notifier.py` | email / webhook notifications |
| `report.py` | `TaskReport` |

The self-improvement pipeline (capture → consolidation → promotion) that turns
learnings into skills is documented in
[core.md](./core.md#16-self-improvement-capture--learningspy); the daemon's
control endpoints are listed in the HTTP API summary above.

## MCP servers

External tool servers are configured under `mcp.servers` (each entry: `id`,
`transport`, `url`/`command`, `enabled`). The engine turns every **enabled**
server into an ADK `McpToolset` and hands it to the agent, so the server's
tools become callable and — with `use_mcp_resources` — its resources readable.

### How the engine wires it

Unlike skills, MCP tools need no hand-written catalog to be *usable*: ADK calls
the toolset's `get_tools()` and injects each MCP tool's name, description and
JSON schema into the model's function declarations automatically. The model
sees them natively, the same way it sees the core tools. The catalog the
engine renders into the prompt is therefore about **resources**, which ADK does
*not* surface to the model on its own — without a list, the agent has no idea
what a server exposes to read.

`AdkEngine._ensure_mcp(approval_mode)` builds the toolsets:

1. For each enabled server, map `entry.transport` to the matching ADK
   connection params (`_mcp_connection_params`): `stdio` →
   `StdioConnectionParams(server_params=StdioServerParameters(command, args))`,
   `sse` → `SseConnectionParams(url)`, `streamable_http` →
   `StreamableHTTPConnectionParams(url)`.
2. Construct `McpToolset(connection_params=..., use_mcp_resources=True,
   require_confirmation=(approval_mode == "always"),
   tool_name_prefix=_sanitize_mcp_prefix(entry.id))`. The prefix namespaces the
   server's tools so they cannot collide with core tool names (`read_file`, …);
   a non-alphanumeric id like `SkillMD.com` folds to `SkillMD_com`.
3. `await toolset.get_tools()` to gather the tool names, and
   `await toolset.list_resources()` for the resource names. The catalog lists
   the **prefixed** tool names — the exact names the model can call — since ADK
   prefixes the function declarations with `tool_name_prefix`. So a server tool
   `search` under id `a` is catalogued (and callable) as `a_search`, and the
   resource tool as `a_load_mcp_resource`.
4. Render the catalog with `render_mcp_block` and pass it into
   `build_static_instructions(..., mcp_entries=...)`, appended after the skill
   block.

### Built once per config generation

The toolsets are built **once** and reused across runs, not per request — MCP
connections are pooled rather than churned. `_ensure_mcp` keys the build on
`(mcp-config-signature, approval_mode)`; if that key is unchanged it returns
immediately. A config reload (`Supervisor.reload_config`) changes the
signature, so the next request rebuilds against the new server list. `mcp` is
in `AGENT_RESTART_KEYS`, so a config change that touches MCP also flows
through the engine's restart path.

`_ensure_mcp` is called at the top of `stream()` and `resume()`, before the
agent is built, so the toolsets and catalog are always current when the agent
is constructed.

### Per-server failure skip

A server whose **tools** fail to load (connection refused, bad transport,
`get_tools()` error) is **skipped, not fatal**: the engine logs a warning,
closes that toolset, and continues with the remaining servers. One dead server
never takes down a run.

The **resource** listing is best-effort and separate. A server that does not
implement the MCP resources capability answers `resources/list` with "Method
not found" — that does **not** discard the server. Its tools are kept and only
the resource catalog is empty. (This is the common case: many MCP servers
expose tools but no resources.) `close()` on each toolset is best-effort and
safe to call repeatedly; `Supervisor.stop()` releases any sessions the engine
holds open.

### Approval mapping

MCP tool calls follow the run's approval mode: when `approval_mode ==
"always"`, the toolset is built with `require_confirmation=True` and every MCP
tool call pauses for confirmation (the same pause/resume round-trip as the
core write tools); otherwise MCP tools run freely.

### Limitation: no per-server auth

The `MCPServerEntry` schema carries no auth fields (no API key, bearer token,
or OAuth). Servers that require authentication are therefore **not yet
supported** — they will fail to connect and be skipped with a warning. Adding
auth means extending the config schema and threading credentials into the
connection params.

---


## File structure

```
claudio-backend/
├── pyproject.toml                  deps + "only the engine knows ADK" contract
└── src/claudio_backend/
    ├── __init__.py
    ├── app.py                      FastAPI assembly (routers, lifespan, CORS)
    ├── supervisor.py               owns config/db/engine/run-manager lifecycle
    ├── errors.py                   backend error types
    ├── paths.py                    data/log path resolution
    ├── engine/                     ★ the ONLY package importing ADK
    │   ├── __init__.py             exports AdkEngine, create_engine
    │   ├── adk_engine.py           the ADK RunEngine implementation
    │   └── reasoning_llm.py        framework-free streaming LLM client
    ├── runs/
    │   ├── __init__.py
    │   └── manager.py              RunManager: start/resume/cancel, finalisation
    ├── api/
    │   ├── deps.py                 require_auth, ApiError
    │   ├── system.py               healthz / readyz / version
    │   ├── chat.py                 chat stream + run control
    │   ├── approvals.py            human-in-the-loop
    │   ├── sessions.py
    │   ├── config.py               GET/PUT (merge-patch) + validate/test
    │   ├── files.py                upload
    │   ├── workspace.py            file/tree views, download
    │   ├── management.py           agent start/stop/restart
    │   ├── monitoring.py           health, metrics, logs, runs
    │   ├── tasks.py                user-defined scheduled playbooks
    │   ├── autonomous.py           daemon control + manual triggers
    │   └── learning.py             self-improvement read API + draft approval
    └── autonomous/                 the watch/maintain/repair daemon
        ├── __init__.py
        ├── service.py              daemon orchestration
        ├── scheduler.py            cron scheduling for built-in passes
        ├── task_scheduler.py       user-defined task scheduling
        ├── task_runner.py          task dispatch (HANDLERS)
        ├── health.py               health-check pass
        ├── maintenance.py          maintenance playbook
        ├── recovery.py             service recovery
        ├── fixer.py                agent-proposed fix commands
        ├── agent.py                autonomous agent helpers
        ├── tools.py                daemon-side tools
        ├── script.py               operator-script runner
        ├── notifier.py             email/webhook notifications
        ├── report.py               TaskReport
        ├── consolidation.py        learnings → skill drafts
        └── promotion.py            draft → live skill (validate + backup)
```

---


## Development

```bash
cd claudio-backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

# The engine imports against core's source tree:
PYTHONPATH=src:../claudio-core/src python -c "
from claudio_backend.engine import create_engine
from claudio_core import AppConfig, Database
import pathlib, tempfile
db = Database(pathlib.Path(tempfile.mkdtemp()) / 't.db')
print(type(create_engine(AppConfig(), db)).__name__)   # AdkEngine
"

.venv/bin/lint-imports    # only the engine knows about ADK
```

---


## ADK integration notes

Facts established empirically against `google-adk` 2.12.0. Several contradict
the documentation and cost real time to discover.

| Thing | Reality |
|---|---|
| `ResumabilityConfig` location | `google.adk.apps._configs` — **not** `google.adk.apps.resumability_config`, which does not exist |
| `App` fields | `name`, `root_agent`, `plugins`, `events_compaction_config`, `context_cache_config`, `resumability_config` |
| OpenAI chat-completions model | `google.adk.integrations.openai.OpenAILlm` |
| `OpenAILlm` fields | `model` (default `gpt-4o`), `max_tokens` (4096), `api_key` (str \| Callable \| AsyncCallable \| None), `base_url`, `client` |
| Extras | `OpenAILlm` needs the `[openai]` extra. `LiteLlm` needs `[extensions]` and raises `ImportError` otherwise |
| `Runner.__init__` | `(*, app, app_name, agent, node, plugins, artifact_service, session_service, memory_service, credential_service, plugin_close_timeout=5.0, auto_create_session=False)` |
| `Runner.run_async` | `(*, user_id, session_id, invocation_id, new_message, state_delta, run_config, yield_user_message, abort_signal)` |
| `SqliteSessionService` | `SqliteSessionService(db_path: str)`; `create_session`, `get_session`, `append_event`, `close`, `flush`, `get_user_state`, `list_sessions` |
| `EventActions` | `skip_summarization`, `state_delta`, `artifact_delta`, `transfer_to_agent`, `transfer_reason`, `escalate`, `requested_auth_configs`, `requested_tool_confirmations`, `compaction`, `end_of_agent`, `agent_state`, `rewind_before_invocation_id`, `route`, `render_ui_widgets`, `set_model_response` |
| `ToolConfirmation` | `hint: str`, `confirmed: bool`, `payload: Any` |
| `google.adk.workflow` | `BaseNode`, `START`, `DEFAULT_ROUTE`, `Edge`, `FunctionNode`, `JoinNode`, `Node`, `node`, `RetryConfig`, `Workflow`, `NodeTimeoutError` |
| `@node(...)` kwargs | `node_like`, `name`, `rerun_on_resume`, `retry_config`, `timeout`, `parallel_worker`, `max_parallel_workers`, `auth_config`, `require_confirmation`, `parameter_binding='state'` |
| `BaseLlm` | abstract `async def generate_content_async(self, llm_request, stream=False)`; subclassing requires declaring `capabilities` outright — the name-based fallback emits `FutureWarning` |
| `LlmResponse` | `content`, `partial`, `turn_complete`, `finish_reason`, `error_code`, `error_message`, `interrupted`, `usage_metadata`, `custom_metadata` |
| `google.genai.types.Part` | `text`, `function_call`, `function_response`, `thought`, `thought_signature`, `tool_call`, `tool_response`, `inline_data`, `media_resolution` |
| `FunctionDeclaration` | no `.required` attribute; `required` lives inside `parameters_json_schema` |
| `openai` version range | `google-adk[openai]` requires `openai>=2.20,<3`. `openai-agents 0.23.1` requires `openai>=3.0.0,<4`. The two are mutually exclusive on paper. |

### The `openai` version conflict

`claudio-backend` depends on `google-adk[openai]`, which pins `openai<3`. The
legacy engine being replaced depends on `openai-agents`, which pins `openai>=3`.
A clean dependency resolve therefore cannot install both.

This mattered during the transition, for the A/B harness, which needed both
engines in one interpreter. In practice `claudio-backend/.venv` resolves to
`openai 2.54.0` and the legacy `openai-agents` path still worked — the
conflict was over-cautious metadata rather than a real runtime
incompatibility, because `openai-agents` uses chat-completions APIs that
exist in both. But pip reported a conflict, and a fresh `pip install` of a
project that required both refused to resolve.

The migration is now complete: the legacy `Runner` calls have been replaced
by `RunEngine.stream()`, the `openai-agents` dependency is gone from the
backend, and so is the conflict. The note is kept for historical context on
why the engine boundary was worth building.

Warnings to expect and ignore:

- `[EXPERIMENTAL] feature FeatureName.JSON_SCHEMA_FOR_FUNC_DECL is enabled`
  (from `llm_request.py`)
- `Skipping missing token usage metadata for agent X and model Y` — emitted when
  a stub model returns no usage

### Durability caveat

ADK's own `ResumabilityConfig` docstring states: *"ADK resumes the invocation
in a best-effort manner: tool call to resume needs to be idempotent because we
only guarantee an at-least-once behavior... Any temporary / in-memory state
will be lost upon resumption."*

That is **less** than the `RunState` persistence Claudio already hand-rolled.
ADK is not a durability upgrade. The reasons for the migration are the workflow
primitives and the vendor neutrality, not durability — and Claudio's own
budget, allowlist, input-filter and compaction layers must be kept because ADK
supplies none of them.
