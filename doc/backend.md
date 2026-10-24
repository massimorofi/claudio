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

The full surface is documented below; it is a frontend contract and does not
change with the engine.

All routes are under `/api/v1`. All except `GET /healthz` require
`Authorization: Bearer <server.auth_token>`.

#### Rotating the auth token

The token is a single shared secret read from `server.auth_token` in
`claudio-backend/config/config.yaml` (a gitignored, local file). The shipped
default `dev-token-change-me` is a placeholder and should be replaced on any
host reachable by more than its operator. To rotate:

1. **Generate a strong secret:**
   ```bash
   python -c "import secrets; print('claudio-' + secrets.token_urlsafe(32))"
   ```
2. **Set it in the config** — edit `server.auth_token` in
   `claudio-backend/config/config.yaml` to the new value. (You can also do it
   live via `PUT /config` with `{"server": {"auth_token": "<new>"}}`, but the
   file is the source of truth across restarts.)
3. **Restart the backend** so the running process picks it up:
   ```bash
   ./backend-stop.sh && ./backend-start.sh
   ```
4. **Update the frontend.** The browser keeps its own copy in
   `localStorage['claudio.auth_token']`. Open the app, set the new token in the
   UI (which writes localStorage), and reload. Until you do, requests carry the
   old token and return `401`.

The change takes effect immediately for new requests; in-flight SSE streams drop
and reconnect. If you rotate and get locked out, the config file is the
fallback — set a known token there and restart.

#### Error envelope

Every non-2xx response uses:

```json
{"error": {"code": "...", "message": "...", "retryable": false, "details": {}}}
```

Raised as `ApiError(status, code, message, retryable, details)`.

#### Chat

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/chat/stream` | `{session_id, message, approval_mode?, files?}` | SSE stream |
| `POST` | `/chat` | `{session_id, message, approval_mode?, files?}` | `{run_id, session_id, final_output, usage}` |
| `POST` | `/chat/runs/{run_id}/cancel` | — | `{cancelled: bool}` |
| `GET` | `/chat/runs/{run_id}` | — | run record |
| `GET` | `/chat/runs/{run_id}/stream?last_event_id=N` | — | SSE stream (replay + live) |

`POST /chat/stream` creates the session if it does not exist (title = first 60
characters of the message), mirrors the user message to the session store with a
`📎 <names>` suffix when files are attached, starts the run, then streams replay
followed by live events. `HEARTBEAT_SECONDS = 15`.

`POST /chat` is the blocking variant: it waits on `record.task` with a timeout
of `llm.timeout_seconds * 2`, and raises **409** `approval_required` if the run
pauses, or **504** `timeout`.

`GET /chat/runs/{run_id}/stream` is the reconnection endpoint. It replays
everything after `last_event_id` from the ring buffer and then continues live, so
a dropped connection loses nothing.

#### Approvals

| Method | Path | Request | Response |
|---|---|---|---|
| `GET` | `/approvals/pending` | — | `{approvals: [{approval_id, run_id, session_id, tool, arguments, reason, requested_at}]}` |
| `POST` | `/approvals/{approval_id}` | `{decision: "approve"\|"deny", reason}` | `{resolved, decision, run_id}` |

Resolving an approval resumes the paused run from its persisted
`resume_state_json`. The endpoint is **idempotent**: a second click on an
already-resolved approval returns the stored decision with
`already_resolved: true` rather than failing, so a double-submit cannot tear down
the run.

#### Sessions

| Method | Path | Notes |
|---|---|---|
| `GET` | `/sessions` | list sessions |
| `GET` | `/sessions/{id}` | session detail, plus `context_usage` computed from stored `last_context_tokens` |
| `POST` | `/sessions/{id}/compact` | manual compaction |
| `GET` | `/sessions/{id}/messages?limit=200` | transcript; runs `repair_orphans` first |
| `GET` | `/sessions/{id}/active_run` | the in-flight run for this session, if any |
| `DELETE` | `/sessions/{id}` | delete one session |
| `POST` | `/sessions/{id}/clear` | clear transcript, keep the session |

#### Config

| Method | Path | Notes |
|---|---|---|
| `GET` | `/config` | current config, secrets redacted, plus `generation` |
| `GET` | `/config/schema` | JSON schema for the settings UI |
| `PUT` | `/config` | **merge-patch** config; returns changed keys and restart requirements |
| `POST` | `/config/validate` | validate a candidate without saving |
| `POST` | `/config/llm/test` | live connectivity test → `{ok, latency_ms, model, error}` |
| `GET` | `/config/history` | saved versions |
| `POST` | `/config/restore` | restore a version |
| `GET` | `/config/raw` | unredacted; gated by `server.expose_raw_config` |

> **`PUT /config` is a merge-patch, not a replace.** The payload is deep-merged
> over the *current* config (`merged = _merge(current, payload)`), so you only
> need to send the keys you want to change — anything you omit keeps its current
> value. Two consequences worth knowing:
>
> - **Secrets:** the client only ever sees redacted secrets (`***`). A `***` in
>   the payload means *"leave this secret unchanged"*, never *"set the secret to
>   `***`"* — `restore_redacted_secrets(merged, current)` substitutes the stored
>   secret back in before validation. To actually change a secret, send its real
>   value.
> - **Validation is whole-config:** the merged result is validated as a complete
>   `AppConfig`. If the merge produces an invalid config, the request returns
>   `422` and **nothing is written** — the existing config is left untouched.
>
> The response carries `changed_keys` (the dotted paths that actually differed)
> and `restart_required` (whether those keys need a process restart to take
> effect).

#### Files and workspace

| Method | Path | Notes |
|---|---|---|
| `POST` | `/files/upload` | multipart upload |
| `GET` | `/workspace/files` | list files |
| `GET` | `/workspace/tree` | tree view |
| `POST` | `/workspace/files/delete` | delete |
| `GET` | `/workspace/files/download` | single-file download |
| `POST` | `/workspace/files/download` | multi-file download (zip) |

#### Agent management

| Method | Path | Notes |
|---|---|---|
| `POST` | `/agent/start` | 202 accepted |
| `POST` | `/agent/stop` | graceful stop |
| `POST` | `/agent/restart` | 202 accepted |
| `GET` | `/agent/status` | `{state, started_at, active_runs, uptime_seconds, config_generation}` |
| `GET` | `/agent/tools` | tool inventory from core |
| `GET` | `/skills` | discovered skills |

#### Monitoring

| Method | Path | Notes |
|---|---|---|
| `GET` | `/monitoring/health` | system health |
| `GET` | `/monitoring/metrics` | metrics snapshot |
| `GET` | `/monitoring/logs` | ring-buffer logs |
| `GET` | `/monitoring/runs` | run history |

#### Autonomous daemon

| Method | Path | Notes |
|---|---|---|
| `GET` | `/autonomous/status` | daemon state |
| `GET` | `/autonomous/health` | latest health check |
| `GET` | `/autonomous/health/history` | history |
| `POST` | `/autonomous/health/run` | run a health check now |
| `POST` | `/autonomous/maintenance/run` | run maintenance |
| `POST` | `/autonomous/recovery/run` | run boot recovery |
| `GET` | `/autonomous/issues` | open issues |
| `POST` | `/autonomous/issues/{id}/acknowledge` | |
| `POST` | `/autonomous/issues/{id}/resolve` | |
| `POST` | `/autonomous/issues/{id}/propose-fix` | |
| `GET` | `/autonomous/issues/{id}/suggestions` | |
| `POST` | `/autonomous/suggestions/{id}/accept` | |
| `POST` | `/autonomous/suggestions/{id}/dismiss` | |
| `POST` | `/autonomous/notify/test` | test email/webhook |
| `GET` | `/autonomous/audit` | audit trail |
| `GET` | `/autonomous/tasks` | list scheduled tasks |
| `POST` | `/autonomous/tasks` | create |
| `PUT` | `/autonomous/tasks/{key}` | update |
| `DELETE` | `/autonomous/tasks/{key}` | delete |
| `POST` | `/autonomous/tasks/{key}/enable` | |
| `POST` | `/autonomous/tasks/{key}/disable` | |
| `POST` | `/autonomous/tasks/{key}/run` | run now |
| `GET` | `/autonomous/tasks/{key}/runs` | execution history |

#### Learning (self-improvement read API)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/learning?status=&category=&q=&limit=100` | list learnings; `q` runs a ranked full-text search, else newest-first |
| `GET` | `/learning/categories` | distinct categories with counts, most common first |
| `GET` | `/learning/stats` | headline counts: `total`, `raw`, `processed`, `promoted` |
| `GET` | `/learning/consolidations?limit=20` | history of consolidation passes |
| `GET` | `/skill-drafts?status=` | list drafts (pending/approved/rejected) |
| `GET` | `/skill-drafts/{id}` | one draft |
| `POST` | `/skill-drafts/{id}/approve` | validate + write the draft into the live skills directory |
| `POST` | `/skill-drafts/{id}/reject` | reject a pending draft (body: `{note}`) |

`limit` on `/learning` is clamped to `[1, 500]`. Approving a draft that
fails validation returns `422 validation_failed` and leaves the draft
rejected. See the **Self-improvement loop** section below for the full
capture → consolidation → promotion pipeline.

#### System

| Method | Path | Notes |
|---|---|---|
| `GET` | `/healthz` | liveness, **no auth** |
| `GET` | `/readyz` | readiness |
| `GET` | `/version` | backend version, protocol version |

---

## Skills

Claudio ships a curated set of Agent Skills in `claudio-backend/skills/` — one
merged skill per core area, plus the `claudio-*` self-management skills. The
discovery/loading mechanism lives in `claudio_core.skill_loader`; the backend's
job is to connect it to the running agent.

### Two-tier loading strategy

Loading every skill's full text into every request would burn tens of thousands
of tokens of context whether or not any of it is relevant. Instead the prompt
uses two tiers:

- **Tier 1 — the catalog (always on).** Skill *names* and one-line
  *descriptions* are rendered into the system prompt every turn
  (`render_skill_block`). For the current 11-skill set this is ~2.5k tokens.
  The model sees what exists and when each applies, without any of the body.
- **Tier 2 — the full body (on demand).** When the agent judges a skill
  relevant to the task at hand, it calls `load_skill(name)` and receives that
  skill's full instructions plus its on-disk directory. Only then does the body
  enter the context. Skills that never get called never cost anything.

This keeps the always-on cost to the catalog and makes the expensive full text
conditional on actual relevance — the minimize-context answer.

### How the engine wires it

In `AdkEngine._build_agent` (`engine/adk_engine.py`), on every request:

1. If `agent.skills_enabled` is true, the engine scans the configured skills
   directory (`resolve_skills_dir(config)` — a relative `agent.skills_dir` is
   anchored to the backend base) with `scan_skills`.
2. The scan's `entries` are passed to `build_static_instructions(...,
   skill_entries=...)`, which renders the Tier-1 catalog into the system
   prompt.
3. The three read-only skill tools from `build_skill_functions(scan)` —
   `list_skills`, `load_skill`, `read_skill_resource` — are registered on the
   agent as ADK `FunctionTool`s with `require_confirmation=False`. They only
   read files inside the skills directory, so they never gate on the run's
   approval mode.

The scan runs per request rather than once at startup, so a skill added or
edited on disk is picked up without restarting the backend. If the scan fails,
the run proceeds with no skills rather than failing.

Note: the engine builds its own system instruction (static prefix + per-turn
nudges + the skill catalog) as an ADK instruction *provider*, so the catalog
and the nudges are re-evaluated every turn. The `RunRequest.instructions`
field is **not** the live prompt path for the ADK engine.

### The skill tools

| Tool | Args | Returns |
|---|---|---|
| `list_skills` | — | `{ok, skills: [{name, description}]}` |
| `load_skill` | `name` | `{ok, name, directory, instructions}` |
| `read_skill_resource` | `name, relative_path` | `{ok, content}` — a file bundled in the skill folder |

`load_skill` returns the skill's absolute `directory` because skill guides
reference their bundled scripts by paths relative to that directory; the agent
runs them through `run_command`. Discovery never imports or executes anything
inside a skill folder — it only reads `SKILL.md`.

### Directory paths: skills vs workspace

The two on-disk directories resolve **relative paths differently** — a
gotcha worth knowing before launching the backend from anywhere but the repo
root.

| Directory | Config key | Relative path anchored to | cwd-dependent? |
|---|---|---|---|
| Skills (`./skills`) | `agent.skills_dir` | backend base (`claudio-backend/`, from the module's own location) | **No** |
| Workspace (`./data/workspace`) | `agent.workspace_dir` | process working directory | **Yes** |

`resolve_skills_dir` computes the backend base from the module's own
location (`Path(__file__).resolve().parents[3]`), so `./skills` always means
`claudio-backend/skills/` no matter where the backend was launched from. The
workspace, by contrast, uses `Path(workspace_dir).resolve()`, which resolves
against the process cwd — so `backend-start.sh` (which does not change
directory) lands the workspace wherever you invoked it from. An **absolute**
path for either is used as-is. To avoid surprises, always launch from the repo
root, or set `workspace_dir` to an absolute path in
`claudio-backend/config/config.yaml`.

A skill is one folder containing a `SKILL.md` (YAML frontmatter + markdown).
Only direct child folders are scanned. `name` must be lowercase-hyphenated
and match the folder name; `description` is required and ≤ 1024 chars; only
`name`, `description`, `license`, `compatibility`, `metadata`,
`allowed-tools` frontmatter keys are accepted. `GET /api/v1/skills` returns
the resolved directory plus every skill with its `valid` status. Full detail:
[`skills-and-workspace.md`](./skills-and-workspace.md).

### Reasoning is persisted to the transcript

Reasoning (`part.thought`) is mirrored into the JSONL session transcript
(`session_store`) as `{"type": "reasoning", "content": ...}` entries in
`RunManager._track`. Previously reasoning lived only in the live SSE stream, so
it vanished from the chat view on a tab switch or reload while the run was
still going. Persisting it means a remounted view restores the reasoning that
has streamed so far; consecutive deltas are stored as separate entries and
merged back into one block on the client.

---

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

## Self-improvement loop

A Reflexion / Mem0-style loop that turns what the agent runs into into
reusable skills. Three phases, each independently gated by
`self_improvement.*` config (the whole feature is **off** until
`self_improvement.enabled = true`).

### Phase 1 — Capture (per run)

`claudio_core.learning.capture_run` runs after every completed run. It
digests the transcript and asks the model for reusable lessons (error /
root cause / validated fix + category + criticality), then
`dedupe_or_insert` either bumps an existing learning's `recurrence_count`
(FTS match) or inserts a fresh `claudio_learnings` row.

**Per-run-slice, not whole-transcript.** `_schedule_capture`
(`runs/manager.py`) hands the extractor only the transcript entries
appended since the last capture, tracked by a durable per-session watermark
`claudio_sessions.learning_captured_entries`:

```
captured = session.learning_captured_entries
slice    = transcript[min(captured, len) :]     # only new entries
session.learning_captured_entries = len(transcript)   # advance BEFORE the task
capture_run(db, slice, config, ...)            # fire-and-forget
```

The watermark is advanced **synchronously, before** the capture task is
spawned, so a later run on the same session sees the updated value
immediately and never re-reads the same entries regardless of how long
extraction takes. This is what stops a single fixed incident from being
re-extracted every run and inflating its `recurrence_count` past the
promotion threshold on phantom repeats. `POST /sessions/{id}/clear` resets
the watermark to 0 alongside the other counters.

### Phase 2 — Consolidation (scheduled / on demand)

`autonomous/consolidation.run_consolidation` (cron:
`self_improvement.consolidation_schedule`, default weekly Mon 04:00 UTC):

1. Gather every `status='raw'` learning (capped at 2000/pass).
2. Group by the extractor's `category`.
3. One LLM pass collapses near-duplicate categories
   (`rocm`/`amd-gpu`/`gpu-driver` → one `amd-gpu` cluster).
4. For each cluster that clears the bar — a learning recurring
   `promotion_threshold` times, or any `critical` learning — draft a
   `SKILL.md` body, merging into the existing skill when one already covers
   the topic.
5. Insert each as a `pending` draft (or promote straight through when
   `auto_promote` is on) and mark the consumed learnings `processed`.
6. Retention sweep: drop raw learnings older than `retention_days`, then
   enforce the `max_learnings` store cap by dropping the oldest remaining
   raw rows. Processed/promoted history is never pruned here.
7. Record a consolidation row + an audit entry.

A **bloat guard** refuses to create *new* skills past `max_auto_skills`
(updates to existing skills are always allowed).

### Phase 3 — Promotion (the safety gate)

`autonomous/promotion.promote_draft` writes an approved draft into the live
skills directory. Before writing it **validates** the candidate by rendering
the `SKILL.md` into a throwaway temp dir and running the same `scan_skills`
the engine uses — so a malformed draft can never land in the catalog and
silently drop out of (or mislead) the prompt. When the target skill already
exists, the existing folder is copied to a timestamped backup under
`<backend>/data/skill-backups/` first (deliberately outside the scan dir,
so backups aren't reported as broken skills), making a bad auto-merge
revertible. The write is atomic (stage in a temp dir on the same filesystem,
then move). On success the draft is `approved` and the skill is live on the
next run (the engine re-scans per request).

### Safety

Learnings are extracted from transcripts containing **untrusted** content.
The extractor and the consolidation drafting prompts treat that content
strictly as data to summarize, never as instructions to follow. The
**operator-approval gate** on promotion is the primary defense. Do **not**
enable `auto_promote` on an untrusted host — it bypasses that gate and lets
the model's distilled lessons reach the system prompt without review.

### Endpoints

Read side: `GET /learning`, `/learning/categories`, `/learning/stats`,
`/learning/consolidations`. Draft gate: `GET /skill-drafts`,
`POST /skill-drafts/{id}/approve`, `POST /skill-drafts/{id}/reject`. See
the HTTP API table above.

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
