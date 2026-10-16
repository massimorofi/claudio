# Claudio Agent — As-Built System Specification

**Status:** This document describes the **effective (as-built) implementation** of Claudio, not the original design intent. Where the original design and the shipped code diverge, this document records what the code actually does and flags anything designed-but-not-wired. It is written to be **reproduction-grade**: a competent engineer could rebuild the system from scratch using only this document plus the repo.

**Scope:** A single-backend agentic system with **two roles** — an interactive **Chat** role and an autonomous **Daemon** role — plus a **React cockpit** that drives both over HTTP. The chat role is the original surface (a general-purpose agent with file/shell/web tools, HITL approvals, sessions, compaction, budgets, skills). The daemon role is the server-management layer added on top: deterministic health checks, scheduled maintenance, boot disaster recovery, user-defined cron tasks, and a hard command-allowlist safety model. Both roles run inside one Python process; the frontend is a static SPA that talks *only* to the backend's HTTP API.

**Companion documents:**
- `claudio_specs.md` — the agentic architecture (loop, context, recovery) the chat role reproduces.
- `claudio_autonomous_specs.md` — the autonomous server-management design (health, maintenance, recovery, tasks, safety) the daemon role reproduces.
- `README.md` — operator quick-start (run, configure, API table).
- strixper (`github.com/massimorofi/strixper`) — working reference for the FastAPI + SSE + OpenAI Agents SDK + React patterns.

---

## 1. Goals and Principles

1. **Backend-first operability.** Every capability the frontend exposes is a plain REST/SSE endpoint that works with `curl`. The frontend is a convenience layer, never the only path.
2. **OpenAI-compatible native.** The agent runs OpenAI-compatible models through the OpenAI Agents SDK via `OpenAIChatCompletionsModel`. Development/testing uses a **local model** (LM Studio at `http://127.0.0.1:1234/v1`, model `qwen3.5-4b-claude-4.6-opus-reasoning-distilled`).
3. **Errors are data, not exceptions.** Tool failures, denials, and interrupts flow back to the model and to the client as structured content. The service never dies from a failed action.
4. **Configuration is explicit and validated.** One config file, schema-validated on load and on write, with a clear split between *hot-reloadable* and *restart-required* settings.
5. **Deterministic verdicts; the LLM interprets, it does not improvise privileged commands.** The daemon's pass/fail decisions (health, maintenance, recovery) are computed from thresholds and fixed playbooks in Python, never by the model. The LLM's role is to *interpret* findings and *propose* commands; a human confirms every irreversible one.
6. **Defense in depth on three independent surfaces.** A positive **command allowlist** (only exact reviewed commands pass), a **catastrophic blocklist** (denylist backstop for freeform strings), and **scoped sudoers** (OS-level enforcement against the `claudio` user). Each guards a different flow; none is trusted alone.
7. **Report-only by default.** The daemon ships `report_only: true`: it observes and reports but mutates nothing. Irreversible actions are never taken without an operator confirming the exact command and supplying the sudo password.
8. **Never weaken the chat.** The autonomy layer is additive; the chat's freedom and the chat's approval model are unchanged.

---

## 2. System Architecture

### 2.1 Components

```
┌────────────────────────────┐        HTTP (REST + SSE)         ┌──────────────────────────────────────┐
│  Frontend (React SPA)      │ ─────────────────────────────▶ │  Backend (one Python process)         │
│  Vite build, no backend    │  ◀───────────────────────────── │  FastAPI + OpenAI Agents SDK          │
│  • AI Chat                 │        versioned /api/v1         │                                      │
│  • Configuration           │                                │  ┌────────────────────────────────┐   │
│  • Workspace               │                                │  │ Supervisor (always up)         │   │
│  • Monitoring              │                                │  │  lifecycle · config · auth     │   │
│  • Autonomous cockpit      │                                │  └───────┬────────────────┬───────┘   │
└────────────────────────────┘                                │        │ owns           │ owns        │
                                                              │  ┌─────▼──────────┐ ┌──▼─────────┐  │
                                                              │  │ Agent Runtime  │ │ Daemon      │  │
                                                              │  │ (Chat role)    │ │ (autonomy)  │  │
                                                              │  │ Runner·tools·  │ │ health·     │  │
                                                              │  │ sessions·HITL  │ │ maintenance·│  │
                                                              │  └─────┬──────────┘ │ recovery·   │  │
                                                              │        │            │ tasks·      │  │
                                                              │        │            │ schedulers  │  │
                                                              │        │            └──┬─────────┘  │
                                                              │   OpenAI-compatible API · MCP      │
                                                              └──────────────────────────────────────┘
```

**Supervisor vs Agent Runtime vs Daemon.** The backend process (the *supervisor*) is always up while the service runs: it owns the HTTP surface, auth, configuration, and monitoring. Inside it sit two managed subsystems:

- The **agent runtime** — the OpenAI Agents SDK machinery (agents, tools, sessions, MCP connections) that powers the interactive chat. It can be started, stopped, and restarted via API without dropping the HTTP surface.
- The **daemon** — the autonomous layer: a health service + interval scheduler, a task runner + cron scheduler, the maintenance and recovery handlers, the command allowlist, and the notifier. Like the runtime, it auto-starts on boot and gates itself on `autonomous.enabled` each cycle, so toggling autonomy needs no restart.

Shell scripts manage the whole backend process; the API manages the runtime and the daemon inside it. In practice the runtime and the daemon both auto-start on boot (`main.py` lifespan calls `supervisor.start()`).

### 2.2 Communication protocol

| Concern | Choice | Rationale |
|---|---|---|
| Request/response | JSON over HTTP, versioned under `/api/v1` | simple, debuggable |
| Streaming answers | **SSE** (`text/event-stream`) | one-way token streaming; survives proxies |
| Long commands | SSE stream per run; run continues if the client disconnects (reconnectable) | agentic runs outlive a mobile connection / a UI reload |
| Polling (monitoring, cockpit) | Plain GET + TanStack Query refetch | cheap, resilient |

All timestamps are ISO-8601 UTC. All IDs are UUIDv4 unless stated otherwise (the autonomous tables use integer autoincrement IDs).

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
| Cron scheduling | `croniter>=2.0` (evaluated in UTC) |
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
croniter>=2.0
```

> **Version discipline:** the SDK evolves. The class/function names used here (`Agent`, `Runner.run`/`run_streamed`, `function_tool`, `RunState`, `OpenAIChatCompletionsModel`, `ModelSettings`, `SQLiteSession`, `@input_guardrail`/`@output_guardrail`, `GuardrailFunctionOutput`, `MCPServerStdio/Sse/StreamableHttp`, `set_tracing_disabled`) were verified against `openai-agents==0.23.1`. Re-verify on any upgrade.

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
│   ├── claudio.db                # sessions, runs, approvals, config history, autonomous tables (+ SDK tables)
│   ├── daemon_sessions.db        # SDK SQLiteSession store for daemon task runs (kept separate from chat)
│   ├── recovery_state.json       # per-boot guard: units already restarted this boot
│   ├── claudio.pid               # supervisor PID
│   ├── sessions/<id>.jsonl       # per-session audit transcript
│   ├── file-history/<session_id>/# per-edit backups + index.json
│   └── logs/backend.log
├── src/claudio/
│   ├── __init__.py               # __version__ = "1.0.0"
│   ├── main.py                   # FastAPI app + lifespan + middleware + routers
│   ├── settings.py               # config load/validate/merge/redact/restart-map
│   ├── db.py                     # aiosqlite wrapper + schema + CRUD + migrations
│   ├── supervisor.py             # runtime + daemon lifecycle state machine
│   ├── agent/
│   │   ├── context.py            # ToolContext (per-run dataclass)
│   │   ├── builder.py            # builds Agent(s) from config (_make_model, _model_settings)
│   │   ├── instructions.py       # system prompt assembly
│   │   ├── guardrails.py         # input/output guardrails
│   │   ├── memory.py             # CLAUDE.md-style instruction discovery
│   │   ├── skill_loader.py       # Agent Skills: SKILL.md discovery + bounded loading
│   │   ├── subagents.py          # spawn_subagent tool (agents-as-tools)
│   │   └── tools/
│   │       ├── registry.py       # tool metadata + approval policy + build_tools
│   │       ├── files.py          # read/write/edit/list/rewind + file-history
│   │       ├── shell.py          # run_command
│   │       ├── web.py            # web_fetch / web_search
│   │       └── planning.py       # todo_write + nag constant
│   ├── sessions/
│   │   ├── store.py              # SDK SQLiteSession factory + JSONL mirror + repair
│   │   └── compaction.py         # Compactor class (wired into the run loop — see §3.6.7)
│   ├── runs/
│   │   ├── manager.py            # active-run registry, cancel, reconnect buffers, recovery wrapper
│   │   └── events.py             # SDK stream events → SSE event mapping
│   ├── autonomous/               # the daemon role (see §3.9)
│   │   ├── health.py             # read-only host diagnostics + deterministic verdict
│   │   ├── service.py            # HealthService (run_once, issue creation)
│   │   ├── scheduler.py          # HealthScheduler (interval loop)
│   │   ├── notifier.py           # email (SMTP) + webhook outbound
│   │   ├── fixer.py              # propose_fix / execute_fix + catastrophic blocklist
│   │   ├── allowlist.py          # positive command allowlist + validators + sudoers generator
│   │   ├── tools.py              # scoped daemon tools + report-only enforcement + elevated exec
│   │   ├── maintenance.py        # scheduled maintenance handler (safe + suggestion actions)
│   │   ├── recovery.py           # boot disaster-recovery handler
│   │   ├── report.py             # TaskReport dataclass (shared report shape)
│   │   ├── task_runner.py        # runs user-defined agent tasks + built-in handlers
│   │   ├── task_scheduler.py     # cron scheduler for tasks + built-in maintenance
│   │   └── cli.py                # `python -m claudio.autonomous.cli …` (systemd entry point)
│   ├── api/
│   │   ├── deps.py               # ApiError, require_auth, error_envelope
│   │   ├── system.py             # /healthz /readyz /version
│   │   ├── management.py         # /agent/*
│   │   ├── config.py             # /config/*
│   │   ├── chat.py               # /chat/*  (SSE)
│   │   ├── approvals.py          # /approvals/*
│   │   ├── sessions.py           # /sessions/*
│   │   ├── monitoring.py         # /monitoring/*
│   │   ├── workspace.py          # /workspace/*
│   │   ├── autonomous.py         # /autonomous/*  (watch + issues + suggestions + run-now)
│   │   └── tasks.py              # /autonomous/tasks/*  (user task CRUD)
│   └── observability/
│       ├── metrics.py            # in-process counters + latency samples
│       └── logging.py            # JSON logs + ring buffer
├── skills/                       # drop-in Agent Skills (each folder has a SKILL.md)
│   ├── docx/SKILL.md
│   ├── pptx/SKILL.md
│   ├── xlsx/SKILL.md
│   ├── pdf/SKILL.md
│   ├── canvas-design/SKILL.md
│   ├── frontend-design/SKILL.md
│   ├── mcp-builder/SKILL.md
│   ├── skill-creator/SKILL.md
│   └── …                         # 20 skills total (see §3.6.10)
└── tests/                        # 263 tests (settings, tools, approval flow, recovery, skills,
                                  #   health, fixer, allowlist, maintenance, disaster recovery,
                                  #   task runner/scheduler/api/db, run-now API)
```

The `deploy/` directory (systemd units, sudoers, installer, run scripts) lives at the **repo root**, not under `backend/` — see §3.9.7.

### 3.3 Process lifecycle and shell scripts (actual)

Single Uvicorn process; v1 is single-process.

| Script | Actual behavior |
|---|---|
| `start.sh` | Reads `server.host`/`server.port` from `config/config.yaml` (falls back to `config.example.yaml`, then `127.0.0.1:8100`) via an inline `python3` snippet. Creates `.venv` if missing (`python3 -m venv` + `pip install -r requirements.txt`). Checks the port is free with `ss`. Launches `PYTHONPATH=src nohup uvicorn claudio.main:app --host H --port P --log-level warning >> data/logs/backend.log 2>&1 &`, writes PID to `data/claudio.pid`, polls `/healthz` for up to 15s (30×0.5s). Exits non-zero with a log tail on failure. |
| `stop.sh` | Sends `SIGTERM` to the PID; waits up to 20s (40×0.5s) for exit; escalates to `SIGKILL`; removes the PID file. No PID file → exits 0. |
| `restart.sh` | `stop.sh` then `start.sh`. |
| `status.sh` | Checks PID alive → `supervisor: UP/DOWN`; checks `/healthz` → `http: OK/UNREACHABLE`; reads `auth_token` from config and calls `GET /api/v1/agent/status` → prints `agent: <state>`. Exit codes: `running`→0, `degraded`→2, anything else→1. |

**Boot sequence (`main.py` lifespan):** load config (for CORS) → `setup_logging` → construct `Supervisor(BASE_DIR)` → `await supervisor.start()` (auto-starts the runtime **and** the daemon) → yield → `await supervisor.stop()`. `set_tracing_disabled(True)` is called at import so the OpenAI trace exporter never tries to upload (we use our own run records).

**Supervisor state machine (`supervisor.py`):** `stopped → starting → running → stopping → stopped`. `start()` loads config, opens the DB, calls `fail_interrupted_runs()` (marks any run left `running` by a crash as `failed`), builds `SessionStore`, `Compactor`, `RunManager`, then the daemon (`Notifier`, `HealthService`, `HealthScheduler`, `TaskRunner`, `TaskScheduler`), starts both schedulers, and connects MCP servers. `stop()` stops the schedulers first (so no new check fires during teardown), cancels active runs, stops MCP, closes the DB. `reload_config()` re-reads config from disk, bumps `config_generation`, and repoints the live `RunManager`, `Compactor`, `HealthService`, `Notifier`, `TaskRunner`, and `TaskScheduler` at the new config object.

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
  fallback_model: null             # secondary model tried on primary failure (see §3.7)
  timeout_seconds: 120
  retry_attempts: 3                # app-level retry count for transient errors (see §3.7)
  price_input_per_mtok: 0.0        # USD per 1M input tokens (cost + budget)
  price_output_per_mtok: 0.0

agent:
  name: claudio
  instructions_file: instructions.md   # relative to config dir; or inline `instructions:`
  max_turns: 50
  max_budget_usd: 5.0              # per-session cumulative budget, enforced (0 = disabled)
  approval_mode: on_request        # off | on_request | always  (validated)
  workspace_dir: ./data/workspace
  context_window: 32768            # used by Compactor threshold (see §3.6.7)
  compaction_reserve: 8192
  max_output_chars: 8000           # per-tool output clip
  strict_tools: false              # strict JSON-schema tool mode (off for local models)
  skills_enabled: true             # expose Agent Skills (list_skills / load_skill / read_skill_resource)
  skills_dir: ./skills             # skills folder; relative paths anchor to the backend dir (see §3.6.10)
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

autonomous:                        # the daemon role (see §3.9)
  enabled: false                   # master switch for the daemon
  report_only: true                # safe default; flip to false only when you trust it
  health_check:
    enabled: true
    interval_minutes: 10
    disk_warn_percent: 85.0
    disk_crit_percent: 95.0
    mem_crit_percent: 95.0
    swap_warn_percent: 80.0
    load_warn_factor: 1.0          # warning when load1 > cores * this
    load_crit_factor: 2.0          # critical when load1 > cores * this
    backup_path: ""                # directory to check for backup freshness (empty disables)
    backup_max_age_hours: 26.0
  maintenance:                     # scheduled maintenance (spec §8)
    enabled: false
    schedule: "0 3 * * *"          # cron, evaluated in UTC (validated via croniter.is_valid)
    vacuum_size: "200M"            # journalctl --vacuum-size value (validated ^\d+[MGT]$)
    temp_dirs: ["/tmp", "/var/tmp"]
    audit_retention_days: 90
    apt_update: true               # safe
    vacuum_logs: true              # safe
    temp_cleanup: true             # safe
    autoremove: true               # irreversible → routed to suggestion/accept flow
    full_upgrade: false            # irreversible → suggestion
    docker_prune: false            # irreversible → suggestion
  recovery:                        # boot disaster recovery (spec §9)
    enabled: false
    expected_services: []          # units that must be running after boot
    restart_allowlist: []          # units the daemon may restart (mirror in sudoers)
  notification:
    enabled: false
    email:
      enabled: false
      smtp_host: ""
      smtp_port: 587
      username: ""
      password: ""                 # redacted in API responses
      from_addr: ""
      to_addr: ""
      use_tls: true
    webhook:
      enabled: false
      url: ""                      # generic webhook; POSTs JSON on WARNING/CRITICAL
  command_allowlist: null          # when set, replaces the built-in allowlist (see §3.9.2)
```

**Hot vs restart split (`settings.py`):**
- `BACKEND_RESTART_KEYS = {server.host, server.port}` — require a full process restart.
- `AGENT_RESTART_KEYS = {mcp}` — require an agent-runtime restart (`POST /agent/restart`).
- Everything else is hot: `reload_config()` updates the live `RunManager.config`, the `Compactor` thresholds, and the daemon's `HealthService`/`Notifier`/`TaskRunner`/`TaskScheduler` config without restart. The schedulers re-read `autonomous.enabled` each cycle, so toggling autonomy takes effect live.

**Secrets (`settings.py`):** `SECRET_PATHS = ["llm.api_key", "server.auth_token", "autonomous.notification.email.password"]` are replaced with `"***"` by `redact_secrets()` in every response. On save, `restore_redacted_secrets()` treats a `"***"` placeholder as "unchanged" so echoing the redacted config back never blanks a stored secret. The only unredacted path is `GET /config/raw`, which is **403 unless `server.expose_raw_config=true`**.

**Validation at load:** `agent.approval_mode` must be `off|on_request|always`; `maintenance.schedule` must be a valid cron (`croniter.is_valid`); `maintenance.vacuum_size` must match `^\d+[MGT]$`; each `command_allowlist` entry is run through `allowlist.validate_command_entry` (absolute path, no shell metacharacters, not catastrophic). A bad value fails config load fast rather than at 3am when a task tries to use it.

### 3.5 HTTP API (actual request/response shapes)

**Conventions.**
- Base path `/api/v1`. JSON everywhere except SSE endpoints.
- Auth: `Authorization: Bearer <server.auth_token>` on every endpoint except `/healthz` (and the root `/`). 401 without it (`deps.require_auth`).
- Error envelope (all non-2xx):

```json
{ "error": { "code": "...", "message": "...", "retryable": false, "details": {} } }
```

**Error codes actually emitted:** `validation_error` (422), `unauthorized` (401), `forbidden` (403), `not_found` (404), `already_exists` (409), `agent_already_running` / `agent_start_failed` (409), `already_resolved` / `already_handled` (409), `no_resume_state` (409), `agent_not_running` (409), `proposal_failed` (502), `timeout` (504), and run-level codes from `_classify_error`: `budget_exceeded`, `max_turns_exceeded`, `llm_auth_error`, `llm_unavailable`, `input_rejected`, `cancelled`, `internal_error`.

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
| GET | `/skills` | `{"directory","enabled","skills":[{directory,name?,description?,valid,error?}]}` — every skill folder with valid/invalid status (see §3.6.10) |

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

`PUT /config` flow: merge-patch candidate over current → `restore_redacted_secrets` → `AppConfig.model_validate` (422 on failure) → compute `changed_keys` (dotted) → `restart_required_for(changed)` → `write_config_atomic` → `db.add_config_version` → `supervisor.reload_config()`. The `autonomous` block is edited here (deep merge-patch), not through the `/autonomous` router.

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
| `run_restarting` | `{reason, attempt}` | A failed attempt is being retried / compacted / fallen back — see §3.7. `reason` is `"compacted"`, `"fallback"`, or an error code; `attempt` is the retry counter. The frontend discards the partial assistant output and re-streams from the last user message. |
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

#### 3.5.8 Autonomous (watch + issues + suggestions + run-now)

| Method | Path | Response |
|---|---|---|
| GET | `/autonomous/status` | `{enabled, report_only, health_check_enabled, interval_minutes, notification_enabled, notification_channels:[…], last_check:{…}|null, open_issues_count, scheduler_running, last_run_at, next_run_at, maintenance_enabled, recovery_enabled, task_count, task_scheduler_running, next_task_fire_at}` |
| GET | `/autonomous/health` | `{"check": <latest health check row>|null}` |
| GET | `/autonomous/health/history` | `?limit=50` → `{"checks":[{id,created_at,verdict,summary,details,duration_ms,source}]}` |
| POST | `/autonomous/health/run` | run one diagnostic pass now → `{id, verdict, summary, duration_ms, checks:[…], new_issue: <id|null>}` |
| POST | `/autonomous/maintenance/run` | run one built-in maintenance pass now → `{"report": <TaskReport>}` |
| POST | `/autonomous/recovery/run` | run one built-in disaster-recovery pass now → `{"report": <TaskReport>}` |
| GET | `/autonomous/issues` | `?status=open|acknowledged|resolved` → `{"issues":[…]}` |
| POST | `/autonomous/issues/{id}/acknowledge` | `{"issue": {…}}` |
| POST | `/autonomous/issues/{id}/resolve` | body `{resolution?}` → `{"issue": {…}}` |
| POST | `/autonomous/notify/test` | send a test through every enabled channel → `{enabled, channels:{email:{ok,error}, webhook:{ok,error}}, ok, message}` |
| POST | `/autonomous/issues/{id}/propose-fix` | ask the agent for one fix command → `{"suggestion": {…}}` (502 `proposal_failed` on error) |
| GET | `/autonomous/issues/{id}/suggestions` | `{"suggestions":[…]}` |
| POST | `/autonomous/suggestions/{id}/accept` | body `{password?}` → run the command, audit, re-check → `{executed:{ok,exit_code,stdout,stderr,duration_ms}, recheck:{verdict,summary}, issue_resolved:bool}` (409 `already_handled` if not pending) |
| POST | `/autonomous/suggestions/{id}/dismiss` | `{"suggestion": {…}}` |
| GET | `/autonomous/audit` | `?limit=100&action=&actor=&since=&until=` → `{"audit":[{id,created_at,actor,action,command,exit_code,detail}]}` |

The `accept` endpoint feeds `password` straight to the command's stdin (`sudo -S`) and deliberately does **not** write it to the audit log, the suggestion record, or captured output. If the post-fix re-check comes back `ok`, the originating issue is auto-resolved.

#### 3.5.9 Tasks (user-defined scheduled playbooks)

| Method | Path | Response |
|---|---|---|
| GET | `/autonomous/tasks` | `{"tasks":[{key,name,prompt,schedule,approval_mode,report_only,notify_on,enabled,last_run_at,next_run_at,last_verdict,created_at,updated_at,next_run}]}` (`next_run` computed on read) |
| POST | `/autonomous/tasks` | body `TaskCreate` → `{"task": {…}}` (409 `already_exists` if key taken) |
| PUT | `/autonomous/tasks/{key}` | body `TaskUpdate` (partial) → `{"task": {…}}` (404 if missing) |
| DELETE | `/autonomous/tasks/{key}` | `{"deleted": <key>}` |
| POST | `/autonomous/tasks/{key}/enable` | `{"task": {…}}` |
| POST | `/autonomous/tasks/{key}/disable` | `{"task": {…}}` |
| POST | `/autonomous/tasks/{key}/run` | run now via `TaskRunner` (`trigger="manual"`) → `{"report": <TaskReport>}` |
| GET | `/autonomous/tasks/{key}/runs` | `?limit=50` → `{"runs":[{id,task_key,started_at,finished_at,verdict,summary,output,exit_code,trigger}]}` |

`TaskCreate` validation: `key` must match `^[a-z0-9][a-z0-9-]*$`; `schedule` must be valid cron (`croniter.is_valid`); `notify_on` values must each be one of `ok|warning|critical|failure`. Every mutation calls `supervisor.task_scheduler.wake()` so the running scheduler recomputes fire times immediately.

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
| `list_skills` | `(ctx)` → `{ok, skills:[{name,description}]}` | read-only (Agent Skills, see §3.6.10) |
| `load_skill` | `(ctx, name)` → `{ok, name, directory, instructions}` | read-only (Agent Skills) |
| `read_skill_resource` | `(ctx, name, relative_path)` → `{ok, content}` | read-only (Agent Skills) |

The three skill tools are added automatically when `agent.skills_enabled=true` (they are not part of the `tools.enabled` registry); `load_skill` returns the skill's **absolute directory** because skill guides reference their bundled scripts by paths relative to it.

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

#### 3.6.7 Context management / compaction — ✅ WIRED

`sessions/compaction.py` contains a complete `Compactor` class: `should_compact(input_tokens)` at `context_window − reserve`, a structured 6-section `SUMMARY_PROMPT`, a `compact()` that runs a one-shot summarizer agent and replaces the session with the summary, and a **circuit breaker** (3 consecutive failures → disable for the session).

Compaction is now **wired into the run loop** by `RunManager._run_with_recovery` (see §3.7), at two points:

1. **Proactive, before the run starts.** `_maybe_compact` estimates the current conversation size and, if `should_compact(est)` and the session is not circuit-broken, runs `compact()` first so the run begins with a trimmed history.
2. **Reactive, on context overflow.** If the provider rejects the prompt with a context-length error (`_is_context_overflow`), the manager compacts once and retries the run on the trimmed history. Reactive compaction is capped at **once per run** so a persistently-overflowing session cannot loop forever.

**Token estimate source.** The compaction trigger uses a **content-length estimate** (`_estimate_items_tokens` = total string characters ÷ 4) computed from `session.get_items()`, not the provider-reported `input_tokens`. This is deliberate: some local providers (observed with LM Studio) return **zero token usage on the streamed path** (`run_streamed` result reports `input_tokens = 0`), which would make a usage-based trigger never fire. The ÷4 heuristic is provider-independent and conservative enough to keep the proactive trigger working regardless of what the provider reports. The persisted `claudio_sessions.last_context_tokens` field records the last known input-token count (real usage when available, otherwise the content estimate) so the next run's proactive check has a starting point.

#### 3.6.8 Subagents (`subagents.spawn_subagent`)

Spawn-and-keep-parent pattern as an agents-as-tools function:
```python
async def spawn_subagent(ctx, agent_id: str, task: str) -> dict:
    child = tctx.agent_factory(agent_id)          # built from config.agent.subagents[agent_id]
    result = await Runner.run(child, task, max_turns=subagent_cfg.max_turns)
    return {"ok": True, "agent": agent_id, "report": result.final_output}
```
`tctx.agent_factory` is supplied by the run manager; `agent_factory("list")` returns the configured subagent ids. The tool is exposed only when `spawn_subagent` is in `agent.tools.enabled`.

#### 3.6.9 Budgets and turn limits — ✅ ENFORCED

- `max_turns` **is** passed to the Runner; `MaxTurnsExceeded` is caught and surfaced as a failed run with `code:"max_turns_exceeded"`.
- **USD budget is enforced per-session, cumulatively.** `agent.max_budget_usd` caps the **total spend of a session** (not a single run). Enforcement has two gates, both in `runs/manager.py`:
  1. **Run-start gate** — before a run begins, the session's accumulated `total_cost_usd` is read as `base_cost`; if `base_cost >= max_budget_usd` the run is refused immediately with `BudgetExceededError` (surfaced as `run_failed` code `budget_exceeded`) and the LLM is never called.
  2. **Mid-run check** — during streaming, after each event the cumulative spend `base_cost + current_run_cost` is recomputed from real usage; if it crosses the cap the run is cancelled with `BudgetExceededError`.
  - `max_budget_usd = 0` disables enforcement entirely (unlimited).
  - `BudgetExceededError` is **terminal**: it is not retried, does not trigger fallback, and does not trigger reactive compaction — `_classify_error` returns `("budget_exceeded", retryable=False)`.
  - **Provider caveat:** the mid-run check relies on real token usage. On providers that report zero usage on the streamed path (see §3.6.7), the mid-run cost stays ~0, so the cap effectively only bites on the run-start gate. This is moot for free/local models (price 0) and correct for paid providers that report usage.

#### 3.6.10 Agent Skills (`skill_loader.py`) — ✅ IMPLEMENTED

Drop-in, untrusted instruction packs following the [Agent Skills](https://agentskills.io) convention: a folder of markdown instructions and reference files — not Python, not a plugin, nothing to install or rebuild. Ported from the strixper `skill_loader` and adapted to Claudio's typed-tool conventions.

**What a skill is.** A direct child folder of `agent.skills_dir` (default `./skills`, anchored to the backend directory so it does not depend on the process cwd). The folder name must be lowercase words joined by hyphens (`^[a-z0-9]+(?:-[a-z0-9]+)*$`, ≤64 chars) and must equal the `name` in its `SKILL.md` frontmatter.

**`SKILL.md` format.** YAML frontmatter delimited by `---` lines, then the markdown body (the instructions the model reads on demand):

```markdown
---
name: incident-triage
description: Structured triage for engine failures and latency regressions.
---

# When to use this skill
...instructions the model reads on demand...
```

- `name` and `description` are **required**; `description` ≤ 1024 chars.
- `license`, `compatibility`, `allowed-tools` are optional **strings**; `metadata` is an optional **mapping**. Any other frontmatter key is rejected.
- Size caps: 100 KB per manifest, 8 KB frontmatter, 100 KB per resource read.

**Discovery (`scan_skills`).** Runs once per agent build. It never imports or executes anything inside a skill folder — it only reads `SKILL.md`. Each folder resolves strictly and must stay inside the skills root (symlink escapes rejected). Valid folders become `Skill` objects; invalid ones are reported with `valid:false` and the reason, so a typo surfaces as an error rather than a silently missing skill. Duplicate names are rejected.

**Progressive disclosure.** Only skill **names + descriptions** go into the system prompt (rendered by `render_skill_block` with an explicit "untrusted guidance" caveat). The full instructions load only when the model calls `load_skill`, keeping the prompt small until a skill is actually relevant.

**Tools.** `build_skill_tools(scan)` returns three read-only tools — `list_skills`, `load_skill`, `read_skill_resource` (signatures in §3.6.2) — added automatically when `agent.skills_enabled=true`. `read_skill_resource` is confined to the skill folder: it rejects empty/absolute paths and any path that resolves outside the folder. `load_skill` returns the skill's **absolute directory** because skill guides reference their bundled scripts by paths relative to it (the agent runs them via `run_command`).

**Endpoint.** `GET /api/v1/skills` returns `{directory, enabled, skills:[…]}` — every folder with its valid/invalid status (see §3.5.2).

**Shipped skills.** 20 skills copied from strixper into `backend/skills/`: `academy-guide`, `algorithmic-art`, `brand-guidelines`, `canvas-design`, `claude-api`, `discernment-nudge`, `doc-coauthoring`, `docx`, `frontend-design`, `internal-comms`, `mcp-builder`, `pdf`, `pptx`, `research-review`, `skill-creator`, `slack-gif-creator`, `theme-factory`, `web-artifacts-builder`, `webapp-testing`, `xlsx`. Several (docx/pptx/pdf) shell out to system tooling (`pandoc`, LibreOffice `soffice`, `pdftoppm`, npm `docx`); they load regardless but need that tooling installed to actually run.

### 3.7 Error handling and recovery — ✅ IMPLEMENTED

A layered recovery wrapper lives in `RunManager._run_with_recovery`, which sits between the run driver and the actual streaming call (`_run_stream`). Every new run and every resumed run goes through it. The wrapper is **bounded** — each recovery mechanism has its own cap so a pathological session cannot loop forever.

**Order of recovery on a failed attempt.** When `_run_stream` raises, the wrapper inspects the exception and tries, in this priority order:

1. **Reactive compaction on context overflow.** If `_is_context_overflow(exc)` (message matches "context length" / "maximum context" / "context_window" / "prompt is too long" / "too many tokens" / "reduce the length") and reactive compaction has **not** already been used this run: compact the session, publish `run_restarting` with `reason:"compacted"`, re-snapshot, and retry. Capped at **once per run**.
2. **App-level retry with exponential backoff.** If the error is retryable (`_classify_error` → `retryable=True`) and `attempt < llm.retry_attempts`: increment `retry_count`, roll the session back to the pre-run snapshot, publish `run_restarting` with the error code as `reason`, sleep `_backoff_seconds(attempt)`, and retry. `retry_attempts` from config now drives this loop.
3. **Fallback model.** If a `llm.fallback_model` is configured and has **not** already been used this run: rebuild the agent with `model_override = fallback_model` (via `build_primary_agent`), roll the session back, publish `run_restarting` with `reason:"fallback"`, and retry. Capped at **once per run**.
4. **Give up.** Otherwise re-raise → run marked `failed`, `run_failed` SSE emitted.

**Backoff.** `_backoff_seconds(attempt) = min(500·2^(attempt−1), 30000)/1000 + uniform(0, 0.25)` — exponential growth capped at 30s plus a small random jitter to avoid thundering-herd synchronization.

**Session rollback.** Before the loop starts, the manager snapshots `session.get_items()`. On every retry / fallback / reactive-compaction restart it restores that snapshot, so a half-failed attempt never leaves dirty partial state in the conversation history.

**Terminal errors.** `BudgetExceededError` and `asyncio.CancelledError` are re-raised immediately — they are never retried, never trigger fallback, never trigger compaction.

**Client retries disabled.** The `AsyncOpenAI` client is constructed with `max_retries=0` so the SDK/openai internal retry does not double up with this app-level policy. The app-level loop is the single source of truth for retries.

**`retry_count` metric** is now incremented on every retry (was previously dead).

**Run-level error classification** — `_classify_error(exc)` maps an exception to `(code, retryable)`: `budget_exceeded` (never retryable), `max_turns_exceeded`, `llm_auth_error` (auth/401/api key), `llm_unavailable` (429/503/connection/timeout), `input_rejected` (guardrail), else `internal_error`.

**Crash recovery** — on boot, `fail_interrupted_runs()` marks any run left `running` as `failed` with `code:"backend_restarted"`.

**Frontend handling.** On `run_restarting` the chat reducer truncates the visible items back to the last user message (dropping the failed attempt's partial assistant/tool output) and keeps `streaming: true`, so the retry re-streams into a clean bubble.

### 3.8 Security (actual)

- **Bearer token required** on all endpoints except `/healthz` and root `/` (`deps.require_auth`).
- **CORS** restricted to `server.cors_origins`.
- **Secrets redaction** in all responses (`redact_secrets`) and a `sk-` scrub marker in logging. The email SMTP password is a redacted secret too (`autonomous.notification.email.password`).
- **Approval gating** for destructive tools (`run_command`, `write_file`, `edit_file`, `rewind_files`) and out-of-workspace file access.
- **Workspace confinement:** file tools resolve paths against `agent.workspace_dir`; out-of-workspace access requires approval under `on_request`.
- **Daemon command safety (three surfaces):** see §3.9.1 — a positive command allowlist, a catastrophic blocklist, and OS-level scoped sudoers. The daemon never accepts a freeform shell string; every elevated action is an exact argv list run with `sudo -n`.
- **Operator sudo password is never stored.** It is entered at accept time and fed to `sudo -S` stdin once; it is not written to the audit log, the suggestion record, or any captured output.
- TLS termination is the operator's concern; the backend binds plain HTTP.

### 3.9 Autonomous subsystem (the daemon role)

The daemon is the server-management layer that keeps an Ubuntu host tidy and recovers it after a crash. It is **deterministic**: verdicts come from thresholds and fixed playbooks, not the LLM. The LLM only *interprets* findings and *proposes* commands; a human confirms every irreversible one. Everything here is gated by `autonomous.enabled` (master) and `autonomous.report_only` (mutation gate).

#### 3.9.1 The three safety surfaces

The daemon's safety rests on three independent layers, each guarding a different flow. None is trusted alone:

1. **Command allowlist** (`autonomous/allowlist.py`) — a **positive gate**. Only exact, reviewed command strings may run elevated. `is_allowed(command, config)` does a whitespace-normalized exact match against the effective allowlist (config override `autonomous.command_allowlist`, else `DEFAULT_ALLOWLIST`). Every entry is validated at config load by `validate_command_entry`: must start with an absolute path, must contain no shell metacharacters (`; | & $ > < \`` newline/CR), and must not match the catastrophic blocklist. Service restarts are gated separately by `recovery.restart_allowlist` via `validate_service_name` (safe unit-name pattern `^[a-z0-9@._-]+$` AND membership in the list). `sudoers_snippet(config)` generates the matching `/etc/sudoers.d/claudio` lines so code and OS stay in sync.
2. **Catastrophic blocklist** (`autonomous/fixer._is_catastrophic`) — a **denylist backstop** for freeform command strings a human reviews (the propose-fix / accept flow). Patterns like `rm -rf /`, `mkfs`, `dd of=/dev/…`, fork bombs. Used both as a backstop on proposals and as a load-time check on allowlist entries.
3. **Scoped sudoers** (`deploy/sudoers.d-claudio`) — **OS-level enforcement** against the `claudio` user. Exact commands only, no wildcards. Safe/unattended commands are `NOPASSWD`; irreversible commands are deliberately **not** `NOPASSWD`, so even if the application gate were bypassed the OS still demands a password.

**Report-only enforcement** is the fourth, orthogonal gate: when `report_only` is true, `build_daemon_tools(report_only=True)` returns **only the read-only tools** (tool-absence is the strongest enforcement — the agent cannot call what it does not have), and `_elevated_exec` refuses every mutation before doing anything (auditing `mutation_refused_report_only`). `effective_report_only(global_flag, task_flag)` is true if **either** the global or the per-task flag is set (conservative composition).

> **Note on `requiretty`:** the host runs `sudo-rs 0.2.13`, which does **not** support the `requiretty` sudoers option. The sudoers file therefore contains no `Defaults !requiretty` line; `sudo -n` works without it under a systemd-run daemon (no controlling TTY).

#### 3.9.2 Health checks (`health.py`) — read-only, deterministic

`HealthChecker` runs a fixed suite of **read-only** diagnostics against the host. No check mutates anything. Each returns a `CheckResult{name, status(ok|warning|critical|skipped), message, value?, threshold?, detail}`. The overall verdict is the **worst** status seen (`ok < warning < critical`; `skipped` never escalates), computed by `overall_verdict` — **not** by the LLM.

Checks: `check_disk` (`df -P`, per-filesystem % against `disk_warn_percent`/`disk_crit_percent`, skipping volatile pseudo-mounts), `check_memory` (`/proc/meminfo`, mem/swap % against `mem_crit_percent`/`swap_warn_percent`), `check_load` (`os.getloadavg` vs `cores × load_*_factor`), `check_failed_units` (`systemctl --failed`), `check_journal_errors` (`journalctl -p err -n 200`), `check_backup_freshness` (newest file under `backup_path` vs `backup_max_age_hours`; empty path → skipped). Subprocess checks take an injectable `runner` so they are unit-testable without a real host; pure Python (`/proc`, `os.getloadavg`) is used where possible.

`HealthService.run_once(source)` runs the suite, persists a `claudio_health_checks` row, and raises a `claudio_issues` row for each non-OK finding (deduped by title). The verdict drives notification: **non-OK verdicts alert; OK runs stay silent** (the fatigue rule).

#### 3.9.3 Health scheduler (`scheduler.py`)

`HealthScheduler` runs the health service on `health_check.interval_minutes` in a background loop. It is always started; it gates itself on `autonomous.enabled` each cycle, so toggling autonomy needs no restart. `run_once` is also callable on demand (`POST /autonomous/health/run`).

#### 3.9.4 Notifier (`notifier.py`)

Outbound notification on every WARNING/CRITICAL verdict (and on failures). Two transports: **email (SMTP)** — STARTTLS optional, auth optional, works headless — and a **generic webhook** (JSON POST `{verdict, summary, timestamp, details}`). Both are best-effort: a failed notification is logged but never raises into the caller, so a broken SMTP config cannot take a health check down. `Notifier.test()` sends a test through every enabled channel and reports per-channel. Gated by `notification.enabled` AND each channel's own `enabled`.

#### 3.9.5 Fix suggestions (`fixer.py`)

The watch-phase fix flow: the operator clicks "Propose a fix" on an issue → `propose_fix(issue, config)` asks the agent for **one** exact command + explanation + risk + reversibility → stored as a `claudio_suggestions` row (status `pending`). The operator reviews the literal command in the cockpit and **Accept** (`POST /autonomous/suggestions/{id}/accept`, supplying the sudo password) or **Dismiss**. `execute_fix(command, password)` runs it via `sudo -S` (password fed to stdin), audits it, and the endpoint re-runs the health check; if the re-check is `ok` the originating issue is auto-resolved. The catastrophic blocklist guards the freeform string.

#### 3.9.6 Maintenance handler (`maintenance.py`) — spec §8

A deterministic, config-driven playbook. `build_actions(config)` builds an ordered action list from the per-action toggles: **safe** actions first (`apt-get update`, `journalctl --vacuum-size`, temp cleanup), then **irreversible** ones (`apt-get -y autoremove`, `apt-get -y upgrade`, `docker system prune -f`).

`run_maintenance(config, db, notifier, runner=None)`:
- **Safe actions** run through the same `sudo -n` + allowlist + audit gate the daemon tools use (`_elevated_exec`). Under `report_only` they are recorded as "would run" and skipped (not failures).
- **Irreversible actions** never run here — each becomes a **suggestion** (`risk="high"`, `reversible=False`, `issue_id=None`) awaiting operator acceptance, plus a `maintenance_suggested` audit row.
- **Deterministic post-checks:** `check_unattended_upgrades` (is the OS's own `unattended-upgrades` enabled? informational, never escalates), and a disk re-check.
- **Audit retention sweep:** deletes `claudio_audit` rows older than `maintenance.audit_retention_days` (best-effort).
- **Verdict:** `critical` if disk is still full after cleanup; else `warning` if any action failed; else `ok`. Non-OK verdicts notify.

The handler is fully injectable: pass a `runner` to drive every subprocess in tests without touching a real host.

#### 3.9.7 Recovery handler (`recovery.py`) — spec §9

A boot-time disaster-recovery playbook. Safe, reversible actions only; the LLM is not involved. `run_recovery(config, db, notifier, runner=None)` walks a fixed checklist **in order**:
1. **Backups first** — `check_backup_freshness`; missing/stale → **CRITICAL** (recovering without a safety net is dangerous).
2. **Failed units** — restart the ones on `recovery.restart_allowlist`, **once**, then recheck. A per-boot guard (`data/recovery_state.json`, keyed by `/proc/sys/kernel/random/boot_id`) records which units were already restarted so a manual re-run does not re-restart them.
3. **Disk full** — vacuum + temp cleanup, then recheck; still full → CRITICAL.
4. **Expected services** (`recovery.expected_services`) down → restart → verify; still down → CRITICAL.
5. **Kernel filesystem errors** — scan the journal for FS-error signatures (EXT4-fs error, remount read-only, I/O error, XFS/btrfs corruption); present → alert immediately (never fsck a live root).

Under `report_only` every restart/cleanup is a reported no-op. Verdict: `critical` if any critical finding, else `warning` if any warning, else `ok`. Non-OK verdicts notify.

> **Unit-name reconciliation:** `systemctl --failed` reports full names (`nginx.service`) while the restart allowlist and sudoers use the bare shorthand (`nginx`). `_normalize_unit` strips a trailing `.service` so the two line up; other unit types (`.socket`, `.mount`) are left as-is.

#### 3.9.8 Scoped daemon tools (`tools.py`) — spec §10.3

Each tool is a named, validated action that maps to one allowlisted command. **There is no `run_command` here** — the daemon never freeforms.
- **Read-only tools:** `check_disk`, `check_memory`, `check_services`, `check_logs`, `verify_backups` (wrap the `HealthChecker` functions).
- **Mutation tools:** `run_updates` (`apt-get update`), `autoremove`, `vacuum_logs` (uses the configured `vacuum_size`), `restart_service(name)` (validated against `restart_allowlist`).
- `_elevated_exec(argv, ctx, action)` is the single gate: **report-only refuse → allowlist check → `sudo -n <argv>` exec → audit** (before/after, with exit code). Each tool returns `{ok, exit_code, stdout, stderr, summary}`.
- `build_daemon_tools(report_only, strict_mode)` returns read-only tools only under report-only, else the full set. Approval is always **off** for the daemon — the allowlist + report-only are the boundary, not the SDK approval gate.
- `DaemonContext{config, db, report_only, runner}` is the runtime context handed to every daemon tool.

#### 3.9.9 Task runner (`task_runner.py`)

Runs two kinds of work:
- **User-defined tasks** (`run(task_key, trigger)`) — a playbook prompt + the scoped daemon tool set, run as a small agent that returns a `TaskReport`. Loads the task row, computes `effective_report_only`, runs `Runner.run` with `max_turns` and a wall-clock `asyncio.wait_for` timeout (900s), coerces the agent's text into a `TaskReport` (tolerant JSON via `fixer._extract_json`), records a `claudio_task_runs` row, updates the task's `last_run_at`/`last_verdict`, notifies per `notify_on`, and audits.
- **Built-in handlers** (`run_builtin(kind, trigger)`) — `maintenance` / `recovery` (config-driven, not task rows), wrapped with a 1800s timeout and an overlap guard. The built-ins notify on their own, so this wrapper adds no second notification.

Both have an **overlap guard** (skip if already running → `verdict="skipped"`). **Daemon agent sessions use the SDK's `SQLiteSession` in a separate file (`daemon_sessions.db`)** so they never touch `claudio_sessions` (the chat session list). Session id is `daemon:<key>:<YYYY-MM-DD>`.

`TaskReport{verdict(ok|warning|critical), summary, report, details}` is the shared report shape (`report.py`).

#### 3.9.10 Task scheduler (`task_scheduler.py`)

A cron-based scheduler mirroring the `HealthScheduler` shape. Each cycle it re-reads the enabled tasks from the DB plus the built-in maintenance schedule, computes the next fire for each with `croniter`, sleeps until the soonest (capped at `MAX_SLEEP_SECONDS=300` so external config changes are noticed even without a `wake()`), and fires whatever is due as a background task. `wake()` interrupts the current sleep so a task created/edited/toggled through the API takes effect immediately. **Missed fires during downtime are skipped** — the next fire is always computed from `now`, never from a stale last-fire, so a machine that slept through three scheduled runs wakes up for the *next* one rather than firing three in a row. Gates on `autonomous.enabled` each cycle; built-in maintenance also gated on `maintenance.enabled`.

#### 3.9.11 CLI (`cli.py`) — systemd entry point

```
python -m claudio.autonomous.cli health
python -m claudio.autonomous.cli maintenance
python -m claudio.autonomous.cli recovery
python -m claudio.autonomous.cli task run <key>
python -m claudio.autonomous.cli sudoers
```
Each command loads the live config + database, runs one pass, prints a short result, and exits. The **exit code mirrors the verdict**: `0 = ok`, `1 = warning`, `2 = critical`, `3 = the command errored`. `sudoers` prints the generated `/etc/sudoers.d/claudio` snippet for the current allowlist + restart list, so the operator can review/install it.

#### 3.9.12 Supervisor wiring

The `Supervisor` owns `Notifier`, `HealthService`, `HealthScheduler`, `TaskRunner`, and `TaskScheduler`. `start()` constructs and starts them (after the chat runtime); `stop()` stops the schedulers first, then cancels runs and closes the DB; `reload_config()` repoints all of them at the freshly-loaded config. The schedulers self-gate on `autonomous.enabled`, so toggling autonomy needs no restart.

### 3.9.13 Deployment (`deploy/`, repo root)

| File | Purpose |
|---|---|
| `claudio.service` | the backend service (Uvicorn), `User=claudio` |
| `claudio-health.service` / `.timer` | periodic health check (interval) |
| `claudio-maintenance.service` / `.timer` | daily maintenance pass (`OnCalendar=*-*-* 03:00:00`, `Persistent=true`). **NOT enabled by default** — the internal `TaskScheduler` already runs maintenance on `maintenance.schedule`; enabling both double-runs. |
| `claudio-recovery.service` | boot disaster recovery (`Type=oneshot`, `After=local-fs.target`, `WantedBy=multi-user.target`). **Enabled at boot** — recovery must run even if the backend is down. |
| `run_health_check.sh` / `run_maintenance.sh` / `run_recovery.sh` | thin wrappers that resolve the backend dir, prefer `.venv/bin/python`, export `PYTHONPATH`, and `exec` the matching CLI subcommand. Exit codes mirror the verdict. |
| `sudoers.d-claudio` | the scoped sudo set (see §3.9.1). Reviewed before install; validated with `visudo -cf`. |
| `install.sh` | operator-run installer: creates the `claudio` user/group, syncs code to `/opt/claudio`, builds the venv, installs + validates the sudoers file, installs all systemd units, enables/starts `claudio.service` + `claudio-health.timer` + `claudio-recovery.service`, and leaves the maintenance timer disabled with a note. |

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

> Note: the original design called for `react-hook-form` + Zod and a schema-driven config form. The shipped config forms use **controlled React state + backend validation** (`POST /config/validate` / `PUT /config`) rather than Zod. `react-hook-form`/`zod` are in `package.json` but the forms are hand-controlled.

### 4.2 Project layout (actual)

```
frontend/
├── index.html
├── public/                       # static assets served as-is
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
    │   └── types.ts              # API + SSE event types (incl. all autonomous types)
    └── features/
        ├── chat/
        │   ├── chatState.ts      # reducer: items[], streaming, runId, usage, pendingApproval
        │   ├── useChat.ts        # send / stop / approve / deny / clear / loadSession / resumeActiveRun
        │   ├── ChatView.tsx      # transcript + composer + session list + restore-on-mount
        │   ├── ToolCard.tsx      # collapsible tool call/result card
        │   └── ApprovalCard.tsx  # approve/deny card
        ├── config/
        │   └── ConfigView.tsx    # LLM / Agent / MCP / Danger tabs, test, save, history, restart
        ├── workspace/
        │   └── WorkspaceView.tsx # browse/delete workspace files
        ├── monitoring/
        │   └── MonitoringView.tsx# Overview / Logs / Runs tabs
        └── autonomous/
            ├── AutonomousView.tsx# 5-tab cockpit shell (Dashboard / Issues / Tasks / Audit / Daemon)
            ├── TasksPanel.tsx    # user-task CRUD + run-now + run history
            ├── AuditPanel.tsx    # audit log with action/actor/time filters
            └── DaemonConfigPanel.tsx  # the autonomous config block + built-in run-now cards
```

### 4.3 Responsive strategy

Mobile-first. Below `md`: single column + bottom tab bar (Chat / Config / Workspace / Monitor / Autonomous). Desktop: persistent left sidebar (collapsible to icons, persisted in `localStorage`). Tool cards collapsed by default (tap to expand). Approval cards render inline (block the composer). Touch targets ≥ 44px.

### 4.4 AI Chat (actual)

- **Streaming rendering:** `token` deltas append to the current assistant bubble; `reasoning` deltas go into a collapsible "Reasoning" block; markdown rendered with GFM.
- **Tool-call cards:** each `tool_call` renders a card (name + args); on `tool_result` it updates to ok/error (`ok = !is_error`), expandable to show args and result.
- **Approval flow:** on `approval_required` an approval card shows the tool, arguments, reason, and Approve/Deny buttons; the run resumes after the POST.
- **Run controls:** Stop button while streaming (calls `/chat/runs/{id}/cancel`); the composer is disabled during streaming.
- **Session management:** new chat, list/switch/delete sessions; title auto-derived from the first message.
- **Reload-safe reattach (added feature):** on mount, `ChatView` reads the persisted active session (`storage.ts`), loads its transcript (`loadSession`), and calls `resumeActiveRun` which queries `GET /sessions/{id}/active_run` and, if a run is still `running`/`awaiting_approval`, reattaches to its live stream via `resumeRunStream`. A "Restoring session…" indicator shows during this. Approving a resumed approval re-enters the streaming state.

### 4.5 Configuration (actual)

Tabs: **LLM** (model, base URL, api key, temperature, max tokens, fallback, timeout, retries, prices + **Test connection**), **Agent** (name, approval mode, max turns, budget, context window, compaction reserve, max output chars, workspace, enabled-tools checkboxes), **MCP** (add/edit/remove stdio/sse/streamable_http servers), **Danger** (clear all sessions). Save → `PUT /config`; if `restart_required` is non-empty, a banner offers **Restart now** (`POST /agent/restart`). History/restore is wired through the config endpoints. (The `autonomous` block is edited in the Autonomous → **Daemon** tab, not here — see §4.8.)

### 4.6 Monitoring (actual)

Tabs: **Overview** (health checks + metric stat cards: active/completed/failed runs, retries, tokens in/out, cost, LLM errors, latency p50/p95), **Logs** (level-filtered tail), **Runs** (table of run id/state/turns/cost/started). All polled every 5s with TanStack Query.

### 4.7 State and data flow

Server state via TanStack Query (status/metrics 5s, config/sessions on demand). SSE is owned by `useChat` (a reducer + connection), not Query. The frontend holds no authoritative state: reload always recovers from the backend (transcript + active-run reattach).

### 4.8 Autonomous cockpit (actual)

The **Autonomous** nav item opens `AutonomousView`, a 5-tab cockpit:

- **Dashboard** — current verdict banner (OK/WARNING/CRITICAL) + last-check summary, stat cards (Daemon enabled, Mode report-only/action, Open issues, Interval, Notifications, Scheduler running), the latest check's per-item breakdown, and a recent-checks table. "Run check now" triggers `POST /autonomous/health/run` and shows the fresh verdict (and any new issue raised).
- **Issues** — filter by status (open/acknowledged/resolved/all); each issue card offers **Propose a fix** → shows the exact proposed command with risk/reversibility badges → **Accept & run** (with a sudo-password field when the command starts with `sudo`) or **Dismiss**. After accept it shows the command result and the post-fix re-check verdict, and whether the issue auto-resolved.
- **Tasks** (`TasksPanel`) — list of user-defined tasks (name, schedule, enabled, last verdict, next run); create/edit form (key, name, prompt, cron, report-only toggle, notify-on checkboxes); enable/disable toggle; **Run now** with a result banner; expandable run history. Hits `GET/POST /autonomous/tasks`, `PUT/DELETE /autonomous/tasks/{key}`, `POST .../enable|disable|run`, `GET .../runs`.
- **Audit** (`AuditPanel`) — filter by action/actor/since/until (snapshot applied on "Apply" to avoid per-keystroke requests); table of audit rows. Hits `GET /autonomous/audit?limit=200&action=&actor=&since=&until=`.
- **Daemon** (`DaemonConfigPanel`) — edits the `autonomous` config block: master switches (`enabled`, `report_only`); two built-in **Run now** cards (maintenance, recovery → `POST /autonomous/maintenance/run` and `/autonomous/recovery/run`); maintenance config (schedule, vacuum size, temp dirs, audit retention, six per-action toggles); recovery config (expected services, restart allowlist); notification config (email fields + webhook + **Test** button → `POST /autonomous/notify/test`); and a read-only command-allowlist viewer. Saves via `PUT /config { autonomous }` (deep merge-patch).

---

## 5. Capability Mapping vs `claudio_autonomous_specs.md` (actual)

| Autonomous capability | Status | Where |
|---|---|---|
| Deterministic health checks (disk/mem/load/units/journal/backup) | ✅ Implemented | `autonomous/health.py` |
| Health verdict (worst-of, not LLM) | ✅ Implemented | `health.overall_verdict` |
| Health interval scheduler | ✅ Implemented | `autonomous/scheduler.py` |
| Email + webhook notification (non-OK only) | ✅ Implemented | `autonomous/notifier.py` |
| Propose-fix / accept-with-sudo / dismiss | ✅ Implemented | `autonomous/fixer.py` + `/autonomous/*` |
| Command allowlist (positive gate) | ✅ Implemented | `autonomous/allowlist.py` |
| Catastrophic blocklist (denylist backstop) | ✅ Implemented | `fixer._is_catastrophic` |
| Scoped sudoers (OS-level) | ✅ Implemented | `deploy/sudoers.d-claudio` |
| Report-only enforcement (tool-absence + exec refuse) | ✅ Implemented | `tools.build_daemon_tools` / `_elevated_exec` |
| Scheduled maintenance (safe + suggestion actions) | ✅ Implemented | `autonomous/maintenance.py` |
| unattended-upgrades check | ✅ Implemented | `maintenance.check_unattended_upgrades` |
| Audit retention sweep | ✅ Implemented | `maintenance` + `db.delete_audit_older_than` |
| Boot disaster recovery (backups/units/disk/services/FS) | ✅ Implemented | `autonomous/recovery.py` |
| Per-boot restart guard | ✅ Implemented | `recovery_state.json` keyed by boot_id |
| User-defined cron tasks (playbook + scoped tools) | ✅ Implemented | `task_runner` + `task_scheduler` |
| Task CRUD + enable/disable/run-now/history | ✅ Implemented | `api/tasks.py` |
| Built-in maintenance/recovery run-now | ✅ Implemented | `api/autonomous.py` (`/maintenance/run`, `/recovery/run`) |
| Task scheduler (cron, wake, missed-fire skip) | ✅ Implemented | `autonomous/task_scheduler.py` |
| CLI (health/maintenance/recovery/task/sudoers) | ✅ Implemented | `autonomous/cli.py` |
| systemd units (health, maintenance, recovery) | ✅ Implemented | `deploy/` |
| Cockpit 5-tab UI | ✅ Implemented | `features/autonomous/` |
| Telegram (§12) | ❌ Deferred | focused follow-up |

## 5b. Capability Mapping vs `claudio_specs.md` (chat role, actual)

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
| Agent Skills (drop-in SKILL.md) | ✅ Implemented | `skill_loader.py` + `list_skills`/`load_skill`/`read_skill_resource` + `GET /skills` (§3.6.10) |
| Config hot/restart + history/restore | ✅ Implemented | `settings.py` + `config.py` |
| Reload-safe run reattach | ✅ Implemented | `active_run` endpoint + `useChat.resumeActiveRun` |
| Compaction | ✅ Implemented | `compaction.py` wired into `RunManager._run_with_recovery` (proactive + reactive) |
| USD budget enforcement | ✅ Implemented | per-session cumulative; run-start gate + mid-run check (§3.6.9) |
| Fallback model | ✅ Implemented | tried once on failure via `model_override` (§3.7) |
| App-level retry/backoff | ✅ Implemented | `min(500·2^n,30000)+jitter` honoring `llm.retry_attempts`; `retry_count` incremented (§3.7) |
| Reactive compaction on overflow | ✅ Implemented | on context-length error, compact once + retry (§3.7) |

---

## 6. Non-functional (actual)

| Area | Status |
|---|---|
| Backend survives kill -9 | ✅ Sessions/approvals/autonomous state persist; interrupted runs marked failed on boot |
| Client disconnect / UI reload | ✅ Run continues; reattach via `active_run` + resume stream |
| OpenAI outage | ✅ Transient errors retried with exponential backoff (`llm.retry_attempts`); optional `fallback_model` tried once; `run_restarting` emitted between attempts (§3.7) |
| Daemon toggling without restart | ✅ Schedulers self-gate on `autonomous.enabled` each cycle; `reload_config()` repoints all daemon components |
| Missed task fires during downtime | ✅ Skipped (next fire computed from `now`), never a burst |
| Irreversible-action safety | ✅ Three surfaces (allowlist + blocklist + sudoers) + report-only + operator confirm with sudo password |
| Notification fatigue | ✅ Non-OK verdicts only; OK runs silent |
| Observability | ✅ run_id → events → usage → cost; audit log of every elevated action; logs ring buffer |
| Config safety | ✅ Schema validation on every write; history + restore; allowlist validated at load |
| Mobile | ✅ 360px usable; responsive shell |
| Concurrency | ✅ Multiple concurrent runs supported (per-run tasks); single-process |

---

## 7. Implementation status & remaining work

**Done and verified (263 backend tests pass):** the full chat role — streaming chat, approvals end-to-end, config CRUD + test, sessions, monitoring, full responsive frontend, reload-safe reattach, the full recovery layer (compaction wiring proactive + reactive, per-session cumulative USD budget, app-level retry with exponential backoff, fallback model, `retry_count` metric), and Agent Skills (drop-in `SKILL.md` discovery, the three read-only skill tools, prompt catalog injection, `GET /skills`) — **and** the full autonomous subsystem: deterministic health checks + scheduler, email/webhook notification, the propose-fix/accept flow, the command allowlist + catastrophic blocklist + scoped sudoers, report-only enforcement, scheduled maintenance (safe + suggestion actions, unattended-upgrades check, audit retention), boot disaster recovery (backups/failed-units/disk/expected-services/kernel-FS, per-boot guard), user-defined cron tasks with full CRUD + run-now + history, the cron task scheduler (wake, missed-fire skip), the CLI, the systemd deploy units, and the 5-tab cockpit UI.

**Remaining (each a small, localized change):**
1. **Telegram (§12)** — deferred to a focused follow-up. The server-side autonomy is complete; the Telegram transport is the one spec section not built.
2. **Schema-driven config form** — optionally render the config form from `GET /config/schema` with react-hook-form + Zod (currently hand-controlled).
3. **Provider usage on streamed path** — the mid-run budget check and the compaction trigger rely on a content-length estimate because some local providers (LM Studio) report zero token usage on `run_streamed`. A provider that reports usage on the streamed path makes both exact; no code change needed, just a provider that supports it.

---

## 8. Reproduction guide (from scratch)

**Prereqs:** Python 3.11+, Node 18+, an OpenAI-compatible endpoint (LM Studio for local dev), Ubuntu for the daemon/deploy.

**Backend:**
1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (or just run `./start.sh`, which does this on first run).
2. `cp config/config.example.yaml config/config.yaml`; set `server.auth_token`, `llm.*` (model/base_url), `agent.workspace_dir`.
3. `./start.sh` → backend on `http://127.0.0.1:8100`. Verify `curl http://127.0.0.1:8100/healthz`.
4. Tests: `PYTHONPATH=src .venv/bin/python -m pytest -q` (263 tests).
5. Skills: drop a folder with a `SKILL.md` into `backend/skills/` (or set `agent.skills_dir`); it is picked up on the next agent build. Verify with `GET /api/v1/skills`.
6. Autonomous: configure the `autonomous` block (or use the cockpit Daemon tab). Run a pass manually with `PYTHONPATH=src .venv/bin/python -m claudio.autonomous.cli health|maintenance|recovery`. Print the matching sudoers with `… cli sudoers`.

**Frontend:**
1. `npm install`.
2. `npm run dev` → `http://localhost:5173` (Vite proxies `/api` → `:8100`).
3. `npm run build` → static bundle in `dist/`.

**Deploy (Ubuntu):**
1. Review `deploy/sudoers.d-claudio` and grant only what you have reviewed. Validate: `sudo visudo -cf deploy/sudoers.d-claudio`.
2. `sudo ./deploy/install.sh` (idempotent). It creates the `claudio` user, installs code to `/opt/claudio`, builds the venv, installs the sudoers + all systemd units, and enables `claudio.service`, `claudio-health.timer`, and `claudio-recovery.service`. The maintenance timer is left disabled (the internal scheduler is primary).
3. Edit `/opt/claudio/backend/config/config.yaml` (auth token, notification, maintenance/recovery blocks), then `sudo systemctl restart claudio`.

**Key implementation invariants to preserve when rebuilding:**
- SSE `tool_result` uses `is_error`; `approval_required` uses `tool` (frontend maps `ok=!is_error`, `toolName=tool`).
- `spawn_subagent` is exposed **only** when explicitly in `agent.tools.enabled`.
- Secrets (`llm.api_key`, `server.auth_token`, `autonomous.notification.email.password`) are redacted in all responses; raw only via `GET /config/raw` gated by `server.expose_raw_config`.
- The run is backend-owned: closing the client never cancels it; `fail_interrupted_runs()` reconciles on boot.
- Config writes are atomic and versioned; `restart_required` is returned as a **list** of actions (`["backend"]`, `["agent"]`, or `[]`).
- The daemon never accepts a freeform shell string; every elevated action is an exact argv list run with `sudo -n`, gated by the allowlist + report-only.
- Daemon task sessions live in a **separate** `daemon_sessions.db`, never in `claudio_sessions`.
- The `claudio_suggestions.issue_id` column is nullable (maintenance suggestions are not tied to a detected issue); the migration rebuilds the table once if it is still NOT NULL.

---

## 9. Assumptions

1. **OpenAI-compatible API models are the primary (v1) provider.** Development/testing uses a local model: `http://127.0.0.1:1234/v1`, model `qwen3.5-4b-claude-4.6-opus-reasoning-distilled` (LM Studio). Multi-provider (Anthropic via LiteLLM) is a v2 extension.
2. "Restart the agent" from the frontend means the **agent runtime** restart (`POST /agent/restart`), not the backend process; process restart is a shell-script/operator operation.
3. Single-user / trusted-network v1: one shared auth token, no per-user accounts.
4. The frontend is served separately from the backend in production; in dev, Vite proxies `/api` to the backend.
5. `claudio_specs.md` (chat) and `claudio_autonomous_specs.md` (daemon) remain the behavioral references; where the SDK and the spec diverge, the spec wins and the divergence is patched in the backend's custom layer.
6. **Local-model caveat:** small local reasoning models (e.g. the 4B dev model) can be verbose and unreliable at directly invoking destructive tools. The approval mechanism itself is verified independently (forced-tool-choice integration test); model behavior is a separate quality concern, not a code defect.
7. **The daemon targets Ubuntu** with `systemd`, `apt`, `journalctl`, and `sudo`. The host observed runs `sudo-rs 0.2.13`, which does not support `requiretty`; the sudoers file is written accordingly.
8. **Cron is evaluated in UTC.** Schedule fields and the displayed next-run times are UTC.
9. **Telegram (§12) is out of scope** for this build — a focused follow-up. The server-side autonomy is complete and transport-agnostic.
