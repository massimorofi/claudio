# Claudio Agent — As-Built System Specification

**Status:** This document describes the **effective (as-built) implementation** of Claudio, not the original design intent. Where the original design (`claudio_specs.md`) and the shipped code diverge, this document records what the code actually does and flags anything designed-but-not-wired. It is written to be **reproduction-grade**: a competent engineer could rebuild the system from scratch using only this document plus the repo.

**Scope:** A two-part agentic system — a **Python backend** built on the [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents) and a **React frontend** — implementing the agentic capabilities described in `claudio_specs.md`, with a strict separation between the two deployables.

**Companion documents:**
- `claudio_specs.md` — the agentic architecture (loop, context, recovery) Claudio reproduces.
- `README.md` — operator quick-start (run, configure, API table).
- strixper (`github.com/massimorofi/strixper`) — working reference for the FastAPI + SSE + OpenAI Agents SDK + React patterns.

---

## 1. Goals and Principles

1. **Two independent deployables.** The backend is a self-contained service that runs, is configured, is monitored, and is driven entirely over HTTP — no frontend required. The frontend is a static SPA that talks *only* to the backend's HTTP API. No shared code, no shared runtime.
2. **Backend-first operability.** Every capability the frontend exposes is a plain REST/SSE endpoint that works with `curl`. The frontend is a convenience layer, never the only path.
3. **OpenAI-compatible native.** The agent runs OpenAI-compatible models through the OpenAI Agents SDK via `OpenAIChatCompletionsModel`. Development/testing uses a **local model** (LM Studio at `http://127.0.0.1:1234/v1`, model `qwen3.5-4b-claude-4.6-opus-reasoning-distilled`).
4. **Errors are data, not exceptions.** Tool failures, denials, and interrupts flow back to the model and to the client as structured content. The service never dies from a failed action.
5. **Configuration is explicit and validated.** One config file, schema-validated on load and on write, with a clear split between *hot-reloadable* and *restart-required* settings.

---

## 2. System Architecture

### 2.1 Components

```
┌────────────────────────────┐        HTTP (REST + SSE)         ┌─────────────────────────────────┐
│  Frontend (React SPA)      │ ─────────────────────────────▶ │  Backend (Python service)        │
│  Vite build, no backend    │  ◀───────────────────────────── │  FastAPI + OpenAI Agents SDK     │
│  • AI Chat                 │        versioned /api/v1         │                                 │
│  • Configuration           │                                │  ┌───────────────────────────┐  │
│  • Monitoring              │                                │  │ Supervisor (always up)    │  │
└────────────────────────────┘                                │  │  lifecycle, config, auth  │  │
                                                              │  └──────────┬────────────────┘  │
                                                              │             │ owns              │
                                                              │  ┌──────────▼────────────────┐  │
                                                              │  │ Agent Runtime (managed)   │  │
                                                              │  │  OpenAI Agents SDK Runner │  │
                                                              │  │  tools · sessions · HITL  │  │
                                                              │  └──────────┬────────────────┘  │
                                                              │             │                   │
                                                              │   OpenAI-compatible API · MCP   │
                                                              └─────────────────────────────────┘
```

**Supervisor vs Agent Runtime.** The backend process (the *supervisor*) is always up while the service runs: it owns the HTTP surface, auth, configuration, and monitoring. The *agent runtime* — the OpenAI Agents SDK machinery (agents, tools, sessions, MCP connections) — is a managed object inside the supervisor that can be **started, stopped, and restarted via API without dropping the HTTP surface**. Shell scripts manage the whole backend process; the API manages the agent runtime inside it. In practice the runtime auto-starts on boot (`main.py` lifespan calls `supervisor.start()`).

### 2.2 Communication protocol

| Concern | Choice | Rationale |
|---|---|---|
| Request/response | JSON over HTTP, versioned under `/api/v1` | simple, debuggable |
| Streaming answers | **SSE** (`text/event-stream`) | one-way token streaming; survives proxies |
| Long commands | SSE stream per run; run continues if the client disconnects (reconnectable) | agentic runs outlive a mobile connection / a UI reload |
| Polling (monitoring) | Plain GET + TanStack Query refetch | cheap, resilient |

All timestamps are ISO-8601 UTC. All IDs are UUIDv4 unless stated otherwise.

---

## 3. Backend — Python

### 3.1 Tech stack (exact)

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI + Uvicorn |
| Agent engine | **OpenAI Agents SDK** `openai-agents==0.23.1` (pinned) |
| Validation | Pydantic v2 |
| Persistence | SQLite via `aiosqlite>=0.20` |
| HTTP client (outbound) | `httpx>=0.27` |
| Config | YAML (`pyyaml>=6.0`) + env-var overrides |
| Tests | pytest + pytest-asyncio (`asyncio_mode=auto`) |

`requirements.txt` (verbatim):
```
openai-agents==0.23.1
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.8
pyyaml>=6.0
aiosqlite>=0.20
httpx>=0.27
```

> **Version discipline:** the SDK evolves. The class/function names used here (`Agent`, `Runner.run`/`run_streamed`, `function_tool`, `RunState`, `OpenAIChatCompletionsModel`, `ModelSettings`, `@input_guardrail`/`@output_guardrail`, `GuardrailFunctionOutput`, `MCPServerStdio/Sse/StreamableHttp`, `set_tracing_disabled`) were verified against `openai-agents==0.23.1`. Re-verify on any upgrade.

### 3.2 Project layout (actual)

```
backend/
├── start.sh / stop.sh / restart.sh / status.sh
├── requirements.txt
├── pytest.ini                    # asyncio_mode=auto, testpaths=tests, pythonpath=src
├── config/
│   ├── config.yaml               # live config (written by config API)
│   ├── config.example.yaml       # template
│   ├── instructions.md           # primary agent system prompt (loaded by config)
│   └── agents/explorer.md        # example subagent instructions
├── data/                         # runtime state (gitignored)
│   ├── claudio.db                # sessions, runs, approvals, config history (+ SDK tables)
│   ├── claudio.pid               # supervisor PID
│   ├── sessions/<id>.jsonl       # per-session audit transcript
│   ├── file-history/<session_id>/# per-edit backups + index.json
│   └── logs/backend.log
├── src/claudio/
│   ├── __init__.py               # __version__
│   ├── main.py                   # FastAPI app + lifespan + middleware + routers
│   ├── settings.py               # config load/validate/merge/redact/restart-map
│   ├── db.py                     # aiosqlite wrapper + schema + CRUD
│   ├── supervisor.py             # agent runtime lifecycle state machine
│   ├── agent/
│   │   ├── context.py            # ToolContext (per-run dataclass)
│   │   ├── builder.py            # builds Agent(s) from config
│   │   ├── instructions.py       # system prompt assembly
│   │   ├── guardrails.py         # input/output guardrails
│   │   ├── memory.py             # CLAUDE.md-style instruction discovery
│   │   ├── subagents.py          # spawn_subagent tool (agents-as-tools)
│   │   └── tools/
│   │       ├── registry.py       # tool metadata + approval policy + build_tools
│   │       ├── files.py          # read/write/edit/list/rewind + file-history
│   │       ├── shell.py          # run_command
│   │       ├── web.py            # web_fetch / web_search
│   │       └── planning.py       # todo_write + nag constant
│   ├── sessions/
│   │   ├── store.py              # SDK SQLiteSession factory + JSONL mirror + repair
│   │   └── compaction.py         # Compactor class (see §3.6.7 — NOT wired)
│   ├── runs/
│   │   ├── manager.py            # active-run registry, cancel, reconnect buffers
│   │   └── events.py             # SDK stream events → SSE event mapping
│   ├── api/
│   │   ├── deps.py               # ApiError, require_auth, error_envelope
│   │   ├── system.py             # /healthz /readyz /version
│   │   ├── management.py         # /agent/*
│   │   ├── config.py             # /config/*
│   │   ├── chat.py               # /chat/*  (SSE)
│   │   ├── approvals.py          # /approvals/*
│   │   ├── sessions.py           # /sessions/*
│   │   └── monitoring.py         # /monitoring/*
│   └── observability/
│       ├── metrics.py            # in-process counters + latency samples
│       └── logging.py            # JSON logs + ring buffer
└── tests/                        # 30 tests (settings, tools, approval flow)
```

### 3.3 Process lifecycle and shell scripts (actual)

Single Uvicorn process; v1 is single-process.

| Script | Actual behavior |
|---|---|
| `start.sh` | Reads `server.host`/`server.port` from `config/config.yaml` (falls back to `config.example.yaml`, then `127.0.0.1:8100`) via an inline `python3` snippet. Creates `.venv` if missing (`python3 -m venv` + `pip install -r requirements.txt`). Checks the port is free with `ss`. Launches `PYTHONPATH=src nohup uvicorn claudio.main:app --host H --port P --log-level warning >> data/logs/backend.log 2>&1 &`, writes PID to `data/claudio.pid`, polls `/healthz` for up to 15s (30×0.5s). Exits non-zero with a log tail on failure. |
| `stop.sh` | Sends `SIGTERM` to the PID; waits up to 20s (40×0.5s) for exit; escalates to `SIGKILL`; removes the PID file. No PID file → exits 0. |
| `restart.sh` | `stop.sh` then `start.sh`. |
| `status.sh` | Checks PID alive → `supervisor: UP/DOWN`; checks `/healthz` → `http: OK/UNREACHABLE`; reads `auth_token` from config and calls `GET /api/v1/agent/status` → prints `agent: <state>`. Exit codes: `running`→0, `degraded`→2, anything else→1. |

**Boot sequence (`main.py` lifespan):** load config (for CORS) → `setup_logging` → construct `Supervisor(BASE_DIR)` → `await supervisor.start()` (auto-starts the runtime) → yield → `await supervisor.stop()`. `set_tracing_disabled(True)` is called at import so the OpenAI trace exporter never tries to upload (we use our own run records).

**Supervisor state machine (`supervisor.py`):** `stopped → starting → running → stopping → stopped`. `start()` loads config, opens the DB, calls `fail_interrupted_runs()` (marks any run left `running` by a crash as `failed`), builds `SessionStore`, `Compactor`, `RunManager`, connects MCP servers. `stop()` cancels active runs, stops MCP, closes the DB. `reload_config()` re-reads config from disk and bumps `config_generation`.

### 3.4 Configuration (actual)

Single source `config/config.yaml`, overridable by env vars `CLAUDIO__SECTION__KEY` (two-level only; values are JSON-parsed so lists/numbers/bools work). Precedence: **defaults < YAML < env**. The config API writes the YAML atomically (temp + `os.replace`) and keeps the last 20 versions in SQLite.

**Full schema and shipped defaults** (`settings.py`):

```yaml
server:
  host: 127.0.0.1                 # restart-required
  port: 8100                      # restart-required
  auth_token: "dev-token-change-me"
  cors_origins: ["http://localhost:5173"]
  expose_raw_config: false         # gates GET /config/raw

llm:
  provider: openai
  model: "qwen3.5-4b-claude-4.6-opus-reasoning-distilled"
  api_key: "local"
  base_url: "http://127.0.0.1:1234/v1"
  temperature: 0.2
  max_tokens: 4096
  fallback_model: null             # CONFIG ONLY — not used (see §3.7)
  timeout_seconds: 120
  retry_attempts: 3                # CONFIG ONLY — not used (see §3.7)
  price_input_per_mtok: 0.0        # USD per 1M input tokens (cost tracking)
  price_output_per_mtok: 0.0

agent:
  name: claudio
  instructions_file: instructions.md   # relative to config dir; or inline `instructions:`
  max_turns: 50
  max_budget_usd: 5.0              # CONFIG ONLY — tracked but NOT enforced (see §3.7)
  approval_mode: on_request        # off | on_request | always  (validated)
  workspace_dir: ./data/workspace
  context_window: 32768            # used by Compactor threshold (see §3.6.7)
  compaction_reserve: 8192
  max_output_chars: 8000           # per-tool output clip
  strict_tools: false              # strict JSON-schema tool mode (off for local models)
  tools:
    enabled: [read_file, write_file, edit_file, list_dir, run_command, web_fetch, web_search, todo_write]
    disabled: []
  subagents:
    explorer:
      description: "Read-only code/file explorer; returns a report only"
      instructions_file: agents/explorer.md
      tools: [read_file, list_dir, web_fetch]
      max_turns: 30

mcp:
  servers: []                      # each: {id, transport(stdio|sse|streamable_http), command[], url, enabled}

monitoring:
  log_level: INFO
  log_retention_days: 7
  metrics_snapshot_interval_seconds: 5
```

**Hot vs restart split (`settings.py`):**
- `BACKEND_RESTART_KEYS = {server.host, server.port}` — require a full process restart.
- `AGENT_RESTART_KEYS = {mcp}` — require an agent-runtime restart (`POST /agent/restart`).
- Everything else is hot: `reload_config()` updates the live `RunManager.config` and the `Compactor` thresholds without restart.

**Secrets (`settings.py`):** `SECRET_FIELDS = {("llm","api_key"), ("server","auth_token")}` are replaced with `"***"` by `redact_secrets()` in every response. The only unredacted path is `GET /config/raw`, which is **403 unless `server.expose_raw_config=true`**.

### 3.5 HTTP API (actual request/response shapes)

**Conventions.**
- Base path `/api/v1`. JSON everywhere except SSE endpoints.
- Auth: `Authorization: Bearer <server.auth_token>` on every endpoint except `/healthz` (and the root `/`). 401 without it (`deps.require_auth`).
- Error envelope (all non-2xx):

```json
{ "error": { "code": "...", "message": "...", "retryable": false, "details": {} } }
```

**Error codes actually emitted:** `validation_error` (422), `unauthorized` (401), `forbidden` (403), `not_found` (404), `agent_already_running` / `agent_start_failed` (409), `already_resolved` (409), `no_resume_state` (409), `agent_not_running` (409), `timeout` (504), and run-level codes from `_classify_error`: `max_turns_exceeded`, `llm_auth_error`, `llm_unavailable`, `input_rejected`, `cancelled`, `internal_error`.

#### 3.5.1 System

| Method | Path | Response |
|---|---|---|
| GET | `/healthz` | `{"status":"ok"}` (no auth; also mounted at root `/healthz` for `start.sh`) |
| GET | `/readyz` | readiness (auth) |
| GET | `/version` | `{"backend":"1.0.0","openai_agents":"0.23.1","protocol":1}` |

Root `GET /` → `{"service":"claudio-backend","docs":"/docs","api":"/api/v1"}`.

#### 3.5.2 Agent management

| Method | Path | Response |
|---|---|---|
| POST | `/agent/start` | 202 `{"state":"running"}`; 409 `agent_already_running` if already running |
| POST | `/agent/stop` | 200 `{"state":"stopped"}` |
| POST | `/agent/restart` | 202 `{"state":"running"}`; 409 `agent_start_failed` on error |
| GET | `/agent/status` | `{"state","started_at","active_runs","uptime_seconds","config_generation"}` |
| GET | `/agent/tools` | `{"tools":[{"name","is_read_only","is_destructive"}]}` (full inventory) |

#### 3.5.3 Configuration

| Method | Path | Response |
|---|---|---|
| GET | `/config` | `{"config": <redacted>, "generation": N, "restart_required": []}` |
| GET | `/config/schema` | JSON Schema of `AppConfig` (`AppConfig.model_json_schema()`) |
| PUT | `/config` | merge-patch over current → `{"config","generation","changed_keys":[...],"restart_required":[...]}` |
| POST | `/config/validate` | `{"ok":true}` or 422 |
| POST | `/config/llm/test` | `{"ok":true,"model","latency_ms","echo"}` or a structured error (422 auth / 503 unavailable) |
| GET | `/config/history` | `{"history":[{id, saved_at, changed_keys}]}` |
| POST | `/config/restore` | `{"id":N}` → `{"config","generation","restored_from","restart_required"}` |
| GET | `/config/raw` | unredacted config; **403 unless `server.expose_raw_config=true`** |

`PUT /config` flow: merge-patch candidate over current → `AppConfig.model_validate` (422 on failure) → compute `changed_keys` (dotted) → `restart_required_for(changed)` → `write_config_atomic` → `db.add_config_version` → `supervisor.reload_config()`.

`POST /config/llm/test` does a real `chat.completions.create` with `max_tokens=16` against the **candidate** settings and classifies failures (`llm_auth_error` / `llm_model_not_found` / `llm_unavailable`).

#### 3.5.4 Chat / commands (the agentic surface)

**POST `/chat/stream`** — send a command, stream the answer as SSE.

Request:
```json
{
  "session_id": "uuid | null",     // null → backend creates a new session, emits it in run_started
  "message": "…",                  // required, non-empty
  "approval_mode": "on_request"    // optional per-message override
}
```
On receipt the backend: ensures the session exists (creates with `title = message[:60]` if new), **appends the user message to the JSONL transcript**, creates a `run_id`, and starts the run. Returns `text/event-stream` with `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.

SSE events (each frame has `id:`, `event:`, `data:`):

| Event | Payload | Meaning |
|---|---|---|
| `run_started` | `{run_id, session_id}` | Run accepted; IDs for cancel/reconnect |
| `token` | `{text}` | Assistant text delta |
| `reasoning` | `{text}` | Reasoning/summary delta (if the model emits it) |
| `tool_call` | `{call_id, name, arguments}` | Tool invocation started |
| `tool_result` | `{call_id, name, output, is_error}` | Tool finished; `is_error` mirrors Claudio §6.3 |
| `approval_required` | `{approval_id, tool, arguments, reason}` | HITL gate — run paused until resolved |
| `handoff` | `{from_agent?, to_agent}` | Agent handoff / update |
| `usage` | `{input_tokens, output_tokens, cost_usd}` | Accounting |
| `run_completed` | `{run_id, session_id, final_output, usage}` | Terminal success |
| `run_failed` | `{run_id, error:{code, message, retryable}}` | Terminal failure |
| `heartbeat` | `{}` | Every 15s — keep-alive |

> **Field-name note (important for reproduction):** `tool_result` carries **`is_error`** (not `ok`), and `approval_required` carries **`tool`** (not `tool_name`). The frontend maps `ok = !is_error` and `toolName = tool`.

**Reconnectability / reload-safety.** The run is owned by the backend, not the connection. If the SSE connection drops (or the UI reloads), the run continues. The client reconnects with `GET /chat/runs/{run_id}/stream?last_event_id=…` and resumes from the per-run ring buffer (last **2000** events). `run_completed`/`run_failed` are always eventually delivered.

Other chat endpoints:

| Method | Path | Response |
|---|---|---|
| POST | `/chat` | Non-streaming: blocks until terminal state, returns `{run_id, session_id, final_output, usage}` (or 409 if it paused for approval, or 504 timeout) |
| POST | `/chat/runs/{run_id}/cancel` | `{"cancelled":true}`; emits `run_failed` with `code:"cancelled"` |
| GET | `/chat/runs/{run_id}` | `{run_id, session_id, state, turns_used, cost_usd, events:[…]}` |

#### 3.5.5 Approvals (HITL)

| Method | Path | Response |
|---|---|---|
| GET | `/approvals/pending` | `{"approvals":[{approval_id, run_id, session_id, tool, arguments, reason, requested_at}]}` |
| POST | `/approvals/{approval_id}` | `{"decision":"approve"|"deny", "reason":"…"}` → `{"resolved":true,"decision","run_id}` |

Approvals are persisted in `claudio_approvals`. Resolving one resumes the paused run from its persisted `RunState` (see §3.6.5). Resolving an already-resolved approval → 409 `already_resolved`.

#### 3.5.6 Sessions

| Method | Path | Response |
|---|---|---|
| GET | `/sessions` | `{"sessions":[{id,title,created_at,updated_at,message_count,total_cost_usd}]}` |
| GET | `/sessions/{id}` | full session row |
| GET | `/sessions/{id}/messages` | `{"messages":[…]}` — JSONL transcript, orphan-repaired |
| GET | `/sessions/{id}/active_run` | `{"active":bool, "run_id"?, "state"?, "last_seq"?}` — the in-flight run for this session (running or awaiting_approval), for UI reattach after reload |
| DELETE | `/sessions/{id}` | `{"deleted":true}` |
| POST | `/sessions/{id}/clear` | `{"cleared":true}` — clears transcript + resets counters, keeps the id |

#### 3.5.7 Monitoring

| Method | Path | Response |
|---|---|---|
| GET | `/monitoring/health` | `{supervisor, agent, llm, db, checks:[{name,status}], overall:"ok"|"degraded"}` |
| GET | `/monitoring/metrics` | `{uptime_seconds, requests_total, runs_total, runs_active, runs_completed_total, runs_failed_total, tokens:{in,out}, cost_usd_total, llm_latency_p50_ms, llm_latency_p95_ms, retry_count, tool_errors_total, llm_errors_total, queue_depth}` |
| GET | `/monitoring/logs` | `?level=&since=&limit=200` → `{"logs":[{ts,level,logger,message}]}` (ring buffer, last 2000) |
| GET | `/monitoring/runs` | `?limit=50` → `{"runs":[{run_id,session_id,state,started_at,turns_used,cost_usd}]}` |

### 3.6 Agent core — mapping to the OpenAI Agents SDK

#### 3.6.1 Agent definition (`builder.build_primary_agent`)

```python
Agent[ToolContext](
    name=config.agent.name,
    instructions=_dynamic_instructions(config),   # async fn, recomputed per turn
    model=OpenAIChatCompletionsModel(model=..., openai_client=AsyncOpenAI(api_key, base_url, timeout)),
    model_settings=ModelSettings(temperature=..., max_tokens=...),
    tools=build_tools(enabled, disabled, strict_mode),
    output_type=str,
    input_guardrails=[input_safety_guardrail],
    output_guardrails=[output_safety_guardrail],
)
```
The `spawn_subagent` tool is appended **only if `"spawn_subagent"` is explicitly in `agent.tools.enabled`** (it is not part of the base registry's raw-function map). This was a deliberate fix: auto-adding it caused a small local model to over-delegate in a loop.

#### 3.6.2 Tools (exact signatures)

All tools are plain `async` functions taking `RunContextWrapper[ToolContext]` first; they are wrapped into `FunctionTool`s by `build_tools` with `name_override`, `strict_mode`, and a per-tool `needs_approval` callable. Every tool returns a `dict` with an `"ok"` boolean and never raises (errors are returned as `{"ok": false, "error": "..."}`).

| Tool | Signature | Meta |
|---|---|---|
| `read_file` | `(ctx, path, start_line?, end_line?)` → `{ok, content}` (line-numbered, clipped) | read-only, path_arg=path |
| `write_file` | `(ctx, path, content)` → `{ok, written, bytes, backup?}` | destructive, path_arg=path |
| `edit_file` | `(ctx, path, old_string, new_string, replace_all?)` → `{ok, edited, replacements, backup?}` | destructive, path_arg=path |
| `list_dir` | `(ctx, path=".")` → `{ok, path, entries:[{name,type,size}]}` | read-only, path_arg=path |
| `rewind_files` | `(ctx, path?)` → `{ok, restored, count}` | destructive |
| `run_command` | `(ctx, command, timeout=60)` → `{ok, exit_code, stdout, stderr}` | destructive |
| `web_fetch` | `(ctx, url, max_chars?)` → `{ok, url, content}` | read-only |
| `web_search` | `(ctx, query, max_results=5)` → `{ok, query, results:[{title,url,snippet}]}` | read-only |
| `todo_write` | `(ctx, items:[{content,status}])` → `{ok, count, items}` | read-only |

**Approval policy (`registry._make_approval_check`):** `off` → never; `always` → always; `on_request` → destructive tools **or** any file path that resolves outside the workspace require approval.

**File history (`files.py`):** every `write_file`/`edit_file` first copies the existing file to `data/file-history/<session_id>/<name>@vN` and records it in `index.json`. `rewind_files` restores the latest backup (one file or all tracked files).

**Output clipping:** tool outputs are truncated to `agent.max_output_chars` with a `[clipped: N chars omitted]` marker.

**Web tools:** `web_fetch` uses `httpx` with redirect-following and a regex HTML→text stripper. `web_search` posts to `https://html.duckduckgo.com/html/` and parses `result__a` / `result__snippet`, unwrapping the `uddg=` redirect. No API key required; best-effort.

#### 3.6.3 Sessions and persistence (`sessions/store.py`)

- **Conversation state:** the SDK's `SQLiteSession(session_id, db_path=data/claudio.db)` — the SDK persists and injects history automatically.
- **Audit transcript:** the backend mirrors to an append-only JSONL per session (`data/sessions/<id>.jsonl`). **User messages are appended at run start** (`chat.py`); **assistant messages are appended on completion** (`manager._finalize_completed`). This is the human/forensic record that powers `GET /sessions/{id}/messages`.
- **Orphan repair (`store.repair_orphans`):** any `tool_call` without a matching `tool_result` gets a synthesized error result appended before the transcript is returned (Claudio §6.7).

#### 3.6.4 Streaming (`runs/events.py`)

`Runner.run_streamed()` → `translate_stream_event(event)` maps SDK events to the SSE protocol of §3.5.4. The mapping keys on the **Responses-API event `.type` string** (not the Python class name), which is stable across SDK internals:

| SDK event | Matched on | SSE event |
|---|---|---|
| `RawResponsesStreamEvent` | `.type == "response.output_text.delta"` | `token` |
| `RawResponsesStreamEvent` | `.type == "response.reasoning_summary_text.delta"` | `reasoning` |
| `RunItemStreamEvent` | `name == "tool_called"` | `tool_call` |
| `RunItemStreamEvent` | `name == "tool_output"` | `tool_result` |
| `RunItemStreamEvent` | `name in ("handoff_requested","handoff_occured")` | `handoff` |
| `AgentUpdatedStreamEvent` | — | `handoff` |

`message_output_created` / `reasoning_item_created` are intentionally ignored (text already streamed via raw deltas). This file is the seam: if the SDK's event shapes change, only this file changes.

#### 3.6.5 Human-in-the-loop (approvals)

Uses the SDK's native approval flow. When a tool's `needs_approval` returns True, the run pauses; `result.interruptions` yields approval items. The backend (`manager._handle_interruption`):
1. `state = result.to_state()`; `state_json = state.to_json()`;
2. persists `resume_state_json` on the run row and sets state `awaiting_approval`;
3. creates a `claudio_approvals` row per interruption;
4. emits `approval_required`.

On `POST /approvals/{id}`: resolve the approval in the DB, load the run's `resume_state_json`, and `RunState.from_json(agent, data)` → `state.approve(item)` or `state.reject(item, rejection_message=…)` → `Runner.run_streamed(...)` resumes exactly. The resumed run reuses the **same `RunRecord`**, so any already-subscribed SSE stream (including one reattached after a reload) keeps receiving the continuation events.

#### 3.6.6 Guardrails (`guardrails.py`)

- **Input guardrail** (`@input_guardrail name="input_safety"`): rejects input over `MAX_INPUT_CHARS = 100_000` and trips on high-confidence prompt-injection regexes (e.g. "ignore previous instructions", "you are now in developer mode", "reveal your system prompt"). Tripwire → run fails with `code:"input_rejected"`.
- **Output guardrail** (`@output_guardrail name="output_safety"`): trips on empty output or leaked-secret patterns (`sk-…`, `AKIA…`, `-----BEGIN … PRIVATE KEY-----`).
- Guardrails run in parallel with the agent (SDK-native).

#### 3.6.7 Context management / compaction — ⚠️ IMPLEMENTED BUT NOT WIRED

`sessions/compaction.py` contains a complete `Compactor` class: `should_compact(input_tokens)` at `context_window − reserve`, a structured 6-section `SUMMARY_PROMPT`, a `compact()` that runs a one-shot summarizer agent and replaces the session with the summary, and a **circuit breaker** (3 consecutive failures → disable for the session).

**However, `compact()` / `should_compact()` are never called from the run loop.** The `Compactor` is constructed by the supervisor and passed to `RunManager`, but `_run_stream` does not invoke it. So **automatic compaction is currently dormant.** Long sessions rely on the model/provider context window only. (This is the single largest gap between the design and the shipped code.)

#### 3.6.8 Subagents (`subagents.spawn_subagent`)

Spawn-and-keep-parent pattern as an agents-as-tools function:
```python
async def spawn_subagent(ctx, agent_id: str, task: str) -> dict:
    child = tctx.agent_factory(agent_id)          # built from config.agent.subagents[agent_id]
    result = await Runner.run(child, task, max_turns=subagent_cfg.max_turns)
    return {"ok": True, "agent": agent_id, "report": result.final_output}
```
`tctx.agent_factory` is supplied by the run manager; `agent_factory("list")` returns the configured subagent ids. The tool is exposed only when `spawn_subagent` is in `agent.tools.enabled`.

#### 3.6.9 Budgets and turn limits — ⚠️ PARTIAL

- `max_turns` **is** passed to the Runner; `MaxTurnsExceeded` is caught and surfaced as a failed run with `code:"max_turns_exceeded"`.
- **USD budget is computed but NOT enforced.** Per-run cost is computed from `usage × price_*_per_mtok` and added to session totals on completion, but there is **no check that cancels a run when `max_budget_usd` is exceeded**. `max_budget_usd` is currently decorative.

### 3.7 Error handling and recovery — ⚠️ SIMPLER THAN DESIGNED

The original design (§3.7 of the prior draft) described a layered recovery system. **What is actually implemented:**

- **OpenAI client default retries** — the `AsyncOpenAI` client retries transient transport errors internally (SDK/openai default). No custom backoff loop is implemented.
- **Run-level error classification** — `_classify_error(exc)` maps an exception to `(code, retryable)`: `max_turns_exceeded`, `llm_auth_error` (auth/401/api key), `llm_unavailable` (429/503/connection/timeout), `input_rejected` (guardrail), else `internal_error`. The run is marked `failed`, persisted, and a `run_failed` SSE event is emitted.
- **Crash recovery** — on boot, `fail_interrupted_runs()` marks any run left `running` as `failed` with `code:"backend_restarted"`.

**What is NOT implemented** (present in config or design only):
- ❌ **Fallback model** — `llm.fallback_model` is never used.
- ❌ **App-level retry policy** — `llm.retry_attempts` is never read; no `min(500·2^n,30000)+jitter` loop.
- ❌ **Reactive compaction on "prompt too long"** — not wired (see §3.6.7).
- ❌ **Budget enforcement** — see §3.6.9.
- ❌ **`retry_count` metric** — `Metrics.inc_retry()` exists but is never called; the counter stays 0.

These are the honest gaps. They are straightforward to add (each is a small, localized change in `runs/manager.py`) and are listed in §7 as the remaining work.

### 3.8 Security (actual)

- **Bearer token required** on all endpoints except `/healthz` and root `/` (`deps.require_auth`).
- **CORS** restricted to `server.cors_origins`.
- **Secrets redaction** in all responses (`redact_secrets`) and a `sk-` scrub marker in logging.
- **Approval gating** for destructive tools (`run_command`, `write_file`, `edit_file`, `rewind_files`) and out-of-workspace file access.
- **Workspace confinement:** file tools resolve paths against `agent.workspace_dir`; out-of-workspace access requires approval under `on_request`.
- TLS termination is the operator's concern; the backend binds plain HTTP.

---

## 4. Frontend — React

### 4.1 Tech stack (actual)

| Concern | Choice |
|---|---|
| Framework | React 18 + TypeScript + Vite 5 |
| Styling | Tailwind CSS v4 (`@tailwindcss/vite`) |
| Server state | TanStack Query v5 |
| SSE client | `@microsoft/fetch-event-source` |
| Markdown | `react-markdown` + `remark-gfm` |
| Icons | lucide-react |
| Routing | react-router v6 |

> Note: the original design called for `react-hook-form` + Zod and a schema-driven config form. The shipped config form uses **controlled React state + backend validation** (`POST /config/validate` / `PUT /config`) rather than Zod. `react-hook-form`/`zod` are in `package.json` but the forms are hand-controlled.

### 4.2 Project layout (actual)

```
frontend/
├── index.html
├── vite.config.ts                # /api proxy → http://127.0.0.1:8100
├── tsconfig.json
├── package.json
└── src/
    ├── main.tsx                  # QueryClientProvider + BrowserRouter
    ├── index.css                 # Tailwind v4 @theme tokens + base styles
    ├── App.tsx                   # responsive shell (sidebar / bottom tab bar) + routes
    ├── api/
    │   ├── client.ts             # typed fetch wrapper, bearer auth, error envelope → ApiRequestError
    │   ├── sse.ts                # openChatStream (POST) + resumeRunStream (GET)
    │   ├── auth.ts               # token in localStorage
    │   ├── storage.ts            # active session id in localStorage
    │   └── types.ts              # API + SSE event types
    └── features/
        ├── chat/
        │   ├── chatState.ts      # reducer: items[], streaming, runId, usage, pendingApproval
        │   ├── useChat.ts        # send / stop / approve / deny / clear / loadSession / resumeActiveRun
        │   ├── ChatView.tsx      # transcript + composer + session list + restore-on-mount
        │   ├── ToolCard.tsx      # collapsible tool call/result card
        │   └── ApprovalCard.tsx  # approve/deny card
        ├── config/
        │   └── ConfigView.tsx    # LLM / Agent / MCP / Danger tabs, test, save, history, restart
        └── monitoring/
            └── MonitoringView.tsx# Overview / Logs / Runs tabs
```

### 4.3 Responsive strategy

Mobile-first. Below `md`: single column + bottom tab bar (Chat / Config / Monitor). Desktop: persistent left sidebar. Tool cards collapsed by default (tap to expand). Approval cards render inline (block the composer). Touch targets ≥ 44px.

### 4.4 AI Chat (actual)

- **Streaming rendering:** `token` deltas append to the current assistant bubble; `reasoning` deltas go into a collapsible "Reasoning" block; markdown rendered with GFM.
- **Tool-call cards:** each `tool_call` renders a card (name + args); on `tool_result` it updates to ok/error (`ok = !is_error`), expandable to show args and result.
- **Approval flow:** on `approval_required` an approval card shows the tool, arguments, reason, and Approve/Deny buttons; the run resumes after the POST.
- **Run controls:** Stop button while streaming (calls `/chat/runs/{id}/cancel`); the composer is disabled during streaming.
- **Session management:** new chat, list/switch/delete sessions; title auto-derived from the first message.
- **Reload-safe reattach (added feature):** on mount, `ChatView` reads the persisted active session (`storage.ts`), loads its transcript (`loadSession`), and calls `resumeActiveRun` which queries `GET /sessions/{id}/active_run` and, if a run is still `running`/`awaiting_approval`, reattaches to its live stream via `resumeRunStream`. A "Restoring session…" indicator shows during this. Approving a resumed approval re-enters the streaming state.

### 4.5 Configuration (actual)

Tabs: **LLM** (model, base URL, api key, temperature, max tokens, fallback, timeout, retries, prices + **Test connection**), **Agent** (name, approval mode, max turns, budget, context window, compaction reserve, max output chars, workspace, enabled-tools checkboxes), **MCP** (add/edit/remove stdio/sse/streamable_http servers), **Danger** (clear all sessions). Save → `PUT /config`; if `restart_required` is non-empty, a banner offers **Restart now** (`POST /agent/restart`). History/restore is wired through the config endpoints.

### 4.6 Monitoring (actual)

Tabs: **Overview** (health checks + metric stat cards: active/completed/failed runs, retries, tokens in/out, cost, LLM errors, latency p50/p95), **Logs** (level-filtered tail), **Runs** (table of run id/state/turns/cost/started). All polled every 5s with TanStack Query.

### 4.7 State and data flow

Server state via TanStack Query (status/metrics 5s, config/sessions on demand). SSE is owned by `useChat` (a reducer + connection), not Query. The frontend holds no authoritative state: reload always recovers from the backend (transcript + active-run reattach).

---

## 5. Capability Mapping vs `claudio_specs.md` (actual)

| Claudio capability | Status | Where |
|---|---|---|
| Agentic loop | ✅ Implemented | SDK `Runner` |
| Tool dispatch + schemas | ✅ Implemented | `tools/registry.py` + `@function_tool` |
| Tool errors as data | ✅ Implemented | every tool returns `{ok:false,...}` |
| Turn limits | ✅ Implemented | `max_turns` → `max_turns_exceeded` |
| Streaming | ✅ Implemented | `runs/events.py` → SSE |
| Sessions / transcripts | ✅ Implemented | `SQLiteSession` + JSONL mirror + orphan repair |
| Token accounting | ✅ Implemented | `usage` summed into metrics |
| File history / rewind | ✅ Implemented | `files.py` + `file-history/` |
| Permission / approval gating | ✅ Implemented | `registry` approval policy + `approvals` endpoints |
| Resumable approval state | ✅ Implemented | `RunState` to/from JSON persisted |
| Guardrails (injection, secret leak) | ✅ Implemented | `guardrails.py` |
| Subagents (spawn-as-tool) | ✅ Implemented | `subagents.spawn_subagent` |
| Todo/plan nag | ✅ Implemented | `todo_write` + `TURNS_SINCE_WRITE_NAG=10` |
| CLAUDE.md / memory | ✅ Implemented | `memory.py` + `instructions.py` |
| Config hot/restart + history/restore | ✅ Implemented | `settings.py` + `config.py` |
| Reload-safe run reattach | ✅ Implemented | `active_run` endpoint + `useChat.resumeActiveRun` |
| **Compaction** | ⚠️ Class built, **not wired** | `compaction.py` (dormant) |
| **USD budget enforcement** | ⚠️ Computed, **not enforced** | cost tracked; no cancel-on-exceed |
| **Fallback model** | ❌ Not implemented | config only |
| **App-level retry/backoff** | ❌ Not implemented | config only |
| **Reactive compaction on overflow** | ❌ Not implemented | — |
| Background shells / cron / full hooks | ❌ Out of scope v1 | — |

---

## 6. Non-functional (actual)

| Area | Status |
|---|---|
| Backend survives kill -9 | ✅ Sessions/approvals persist; interrupted runs marked failed on boot |
| Client disconnect / UI reload | ✅ Run continues; reattach via `active_run` + resume stream |
| OpenAI outage | ⚠️ Errors surfaced as `llm_unavailable`; no automatic fallback/retry loop |
| Observability | ✅ run_id → events → usage → cost; logs ring buffer |
| Config safety | ✅ Schema validation on every write; history + restore |
| Mobile | ✅ 360px usable; responsive shell |
| Concurrency | ✅ Multiple concurrent runs supported (per-run tasks); single-process |

---

## 7. Implementation status & remaining work

**Done and verified:** backend (30 tests pass), streaming chat, approvals end-to-end, config CRUD + test, sessions, monitoring, full responsive frontend, reload-safe reattach.

**Remaining (each a small, localized change):**
1. **Wire compaction** — call `compactor.should_compact()` / `compact()` in `RunManager._run_stream` after each turn using the latest `usage.input_tokens`.
2. **Enforce USD budget** — after computing cost each turn, if `record.cost_usd > max_budget_usd`, cancel with `code:"budget_exceeded"`.
3. **Fallback model** — on non-retryable primary failure, rebuild the run with `llm.fallback_model`.
4. **App-level retry** — implement the `min(500·2^n,30000)+jitter` loop honoring `llm.retry_attempts`, and call `metrics.inc_retry()`.
5. **Schema-driven config form** — optionally render the config form from `GET /config/schema` with react-hook-form + Zod (currently hand-controlled).

---

## 8. Reproduction guide (from scratch)

**Prereqs:** Python 3.11+, Node 18+, an OpenAI-compatible endpoint (LM Studio for local dev).

**Backend:**
1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (or just run `./start.sh`, which does this on first run).
2. `cp config/config.example.yaml config/config.yaml`; set `server.auth_token`, `llm.*` (model/base_url), `agent.workspace_dir`.
3. `./start.sh` → backend on `http://127.0.0.1:8100`. Verify `curl http://127.0.0.1:8100/healthz`.
4. Tests: `PYTHONPATH=src .venv/bin/python -m pytest -q` (30 tests).

**Frontend:**
1. `npm install`.
2. `npm run dev` → `http://localhost:5173` (Vite proxies `/api` → `:8100`).
3. `npm run build` → static bundle in `dist/`.

**Key implementation invariants to preserve when rebuilding:**
- SSE `tool_result` uses `is_error`; `approval_required` uses `tool` (frontend maps `ok=!is_error`, `toolName=tool`).
- `spawn_subagent` is exposed **only** when explicitly in `agent.tools.enabled`.
- Secrets (`llm.api_key`, `server.auth_token`) are redacted in all responses; raw only via `GET /config/raw` gated by `server.expose_raw_config`.
- The run is backend-owned: closing the client never cancels it; `fail_interrupted_runs()` reconciles on boot.
- Config writes are atomic and versioned; `restart_required` is returned as a **list** of actions (`["backend"]`, `["agent"]`, or `[]`).

---

## 9. Assumptions

1. **OpenAI-compatible API models are the primary (v1) provider.** Development/testing uses a local model: `http://127.0.0.1:1234/v1`, model `qwen3.5-4b-claude-4.6-opus-reasoning-distilled` (LM Studio). Multi-provider (Anthropic via LiteLLM) is a v2 extension.
2. "Restart the agent" from the frontend means the **agent runtime** restart (`POST /agent/restart`), not the backend process; process restart is a shell-script/operator operation.
3. Single-user / trusted-network v1: one shared auth token, no per-user accounts.
4. The frontend is served separately from the backend in production; in dev, Vite proxies `/api` to the backend.
5. `claudio_specs.md` remains the behavioral reference; where the SDK and the spec diverge, the spec wins and the divergence is patched in the backend's custom layer.
6. **Local-model caveat:** small local reasoning models (e.g. the 4B dev model) can be verbose and unreliable at directly invoking destructive tools. The approval mechanism itself is verified independently (forced-tool-choice integration test); model behavior is a separate quality concern, not a code defect.
