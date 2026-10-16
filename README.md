# Claudio

An agentic assistant with a fully separated **Python backend** (OpenAI Agents SDK)
and **React frontend** (mobile/web). The backend is an independently managed
service — start/stop/configure it with shell scripts or over HTTP; the frontend is
a thin client that talks to it.

```
claudio/
├── backend/     Python service (FastAPI + OpenAI Agents SDK)
│   └── skills/  Drop-in Agent Skills (each folder has a SKILL.md)
├── frontend/    React + TypeScript + Vite + Tailwind client
├── claudio_specs_agent.md    Claudio implementation specs inspired by Claude Code
└── claudio_specs_workflow.md Execution plan / workflow view spec (implemented)
```

## Architecture

Two deployables, clean separation:

- **Backend** (`backend/`) — a FastAPI app wrapping the OpenAI Agent SDK. A
  long-lived **Supervisor** owns the process lifecycle (start/stop/restart), the
  SQLite database, the config store, and the **RunManager** that drives agent
  runs and streams events over SSE. Exposes a versioned JSON API under
  `/api/v1` plus a server-sent-events chat stream.
- **Frontend** (`frontend/`) — a Vite SPA. Three views: **Chat** (streaming,
  tool-call cards, human-in-the-loop approvals), **Config** (LLM / agent / MCP
  settings, test connection, history/restore), **Monitor** (health, metrics,
  logs, runs). Responsive: bottom tab bar on mobile, sidebar on desktop.

The frontend proxies `/api` → `http://127.0.0.1:8100` in dev (see
`frontend/vite.config.ts`), so the two stay independent deployables.

## Prerequisites

- Python 3.11+
- Node 18+
- An OpenAI-compatible LLM endpoint. For local dev this is **LM Studio** at
  `http://127.0.0.1:1234/v1` serving
  `qwen3.5-4b-claude-4.6-opus-reasoning-distilled`.

## Quick start

### 1. Backend

```bash
cd backend
./start.sh          # creates venv, launches uvicorn, waits for /healthz
./status.sh         # supervisor / http / agent status (exit 0 healthy)
./stop.sh           # graceful stop
./restart.sh        # stop + start
```

The first `start.sh` creates `.venv` and installs `requirements.txt`. Config
lives in `config/config.yaml` (copy from `config/config.example.yaml`).

### 2. Frontend

```bash
cd frontend
npm install
npm run dev         # http://localhost:5173
#or
npm run dev -- --host
 VITE v5.4.21  ready in 176 ms

  ➜  Local:   http://localhost:5174/
  ➜  Network: use --host to expose
  ➜  press h + enter to show help

```

Open http://localhost:5173. Default auth token is `dev-token-change-me`
(set in `config/config.yaml` → `server.auth_token`).

## Configuration

Config is YAML with environment overrides (`CLAUDIO__SECTION__KEY`, e.g.
`CLAUDIO__LLM__MODEL`). Secrets (`llm.api_key`, `server.auth_token`) are
**redacted** in every API response and log.

- **Hot-reload** keys (approval mode, budgets, tools, …) apply on save.
- **Restart-required** keys (`server.host`, `server.port`, `mcp.*`) prompt the
  UI to restart the agent.

Edit via the **Config** view (with a live *Test connection* check) or directly:

```bash
# Validate without writing
curl -X POST http://127.0.0.1:8100/api/v1/config/validate \
  -H "Authorization: Bearer dev-token-change-me" \
  -H "Content-Type: application/json" \
  -d '{"llm":{"temperature":0.3}}'

# Save (merge-patch)
curl -X PUT http://127.0.0.1:8100/api/v1/config \
  -H "Authorization: Bearer dev-token-change-me" \
  -H "Content-Type: application/json" \
  -d '{"llm":{"model":"qwen3.5-4b-claude-4.6-opus-reasoning-distilled"}}'
```

## HTTP API (v1)

All endpoints require `Authorization: Bearer <token>` except `/healthz`.
Errors use a consistent envelope: `{ "error": { "code", "message", "retryable", "details" } }`.

| Area | Endpoints |
|------|-----------|
| System | `GET /healthz`, `GET /readyz`, `GET /version` |
| Agent | `POST /agent/start`, `POST /agent/stop`, `POST /agent/restart`, `GET /agent/status`, `GET /agent/tools` |
| Skills | `GET /skills` (list installed skills with valid/invalid status) |
| Config | `GET /config`, `PUT /config`, `GET /config/schema`, `POST /config/validate`, `POST /config/llm/test`, `GET /config/history`, `POST /config/restore`, `GET /config/raw` |
| Chat | `POST /chat/stream` (SSE), `POST /chat` (non-streaming), `POST /chat/runs/{id}/cancel`, `GET /chat/runs/{id}`, `GET /chat/runs/{id}/stream` |
| Approvals | `GET /approvals/pending`, `POST /approvals/{id}` |
| Sessions | `GET /sessions`, `GET /sessions/{id}`, `GET /sessions/{id}/messages`, `DELETE /sessions/{id}`, `POST /sessions/{id}/clear`, `POST /sessions/{id}/compact` |
| Monitoring | `GET /monitoring/health`, `GET /monitoring/metrics`, `GET /monitoring/logs`, `GET /monitoring/runs` |

### Streaming chat

`POST /api/v1/chat/stream` with `{ session_id, message, approval_mode }` returns
`text/event-stream`. Events: `run_started`, `token`, `reasoning`, `tool_call`,
`tool_result`, `approval_required`, `usage`, `context_usage`, `run_completed`,
`run_failed`, `heartbeat`. Each event carries an `id`; on disconnect the client
resumes with `GET /chat/runs/{id}/stream?last_event_id=…` so no events are lost.

**Reload-safe:** a run is owned by the backend, not the browser tab. If you close
or reload the UI while a run is in flight, the agent keeps working. On the next
load the UI reopens the last session (`GET /sessions/{id}/active_run`) and
reattaches to the live stream, so you see the run continue — including any
approval it paused on.

### Human-in-the-loop approvals

When a destructive tool (or any tool, per `approval_mode`) is invoked, the run
pauses, the state is persisted, and an `approval_required` event is emitted.
Approve or deny via `POST /approvals/{id}`; the run resumes from the saved
state.

## Agent capabilities

Per `claudio_specs_agent.md`: file tools (`read_file`, `write_file`, `edit_file`,
`list_dir`, `rewind_files`), `run_command`, `web_fetch`, `web_search`,
`todo_write`, subagent spawning, context compaction with circuit breakers,
file-history backups, USD budget tracking, input/output guardrails,
configurable approval gating, and **Agent Skills** (see below).

## Workflow view

Every turn carries a visible progress surface so the agent's work is legible at a
glance, not just through its prose (full design in `claudio_specs_workflow.md`):

- **Plan tracker** — a pinned checklist of the agent's intent, fed by `todo_write`
  and updated live as item statuses change (`☐ pending · ◐ in progress · ☑ done`).
- **Workflow panel** — one collapsible, numbered trace of every tool call the turn
  made, with a status icon per step and full args/output on expand. Approvals
  render inline as steps.
- **Context gauge** — a donut showing context used vs free against the usable
  window, with the auto-compact threshold marked. Click it to **compact manually**
  (`POST /sessions/{id}/compact`); it updates live from the `context_usage` event.
- **Completion card** — an always-visible bookend on every turn: ✓ Completed /
  ✗ Failed / ⏸ Interrupted, listing files written and commands run, with Retry on
  failed/interrupted. This makes an action-only turn (files written, no prose)
  explicit.

All of it reconstructs after a page reload: tool calls and turn status are
mirrored to the session's JSONL transcript, so closing the tab mid-run and coming
back shows the same workflow, plan, and status.

## Skills

Claudio supports [Agent Skills](https://agentskills.io): drop-in folders of
markdown instructions and reference files — not Python, not a plugin, nothing to
install or rebuild. Twenty skills ship in `backend/skills/` (docx, pptx, xlsx,
pdf, canvas-design, frontend-design, mcp-builder, skill-creator, and more).

When `agent.skills_enabled` is `true` (the default), the agent gains three
read-only tools:

- `list_skills` — names and descriptions of installed skills
- `load_skill` — a skill's full instructions plus its on-disk directory
- `read_skill_resource` — a bounded read of a file bundled inside a skill folder

Only names and descriptions go into the system prompt; the full instructions load
on demand, so the prompt stays small until a skill is actually relevant. Skills
are **untrusted guidance**: they teach the model *what* to do, never grant
permissions, and cannot execute anything on their own.

### Adding a skill

1. Create `backend/skills/<skill-name>/`. The folder name must be lowercase words
   joined by hyphens, e.g. `incident-triage`.
2. Add `SKILL.md` with YAML frontmatter:

   ```markdown
   ---
   name: incident-triage
   description: Structured triage for engine failures and latency regressions.
   ---

   # When to use this skill
   ...instructions the model reads on demand...
   ```

   `name` and `description` are required; `name` must equal the folder name.
   `license`, `compatibility` and `allowed-tools` are optional strings, and
   `metadata` is an optional mapping. Anything else is rejected.
3. Put any reference files in the same folder. The agent reaches them through
   `read_skill_resource`, which is confined to that folder.
4. The skill is picked up on the next agent build. `GET /api/v1/skills` reports
   every valid skill and every invalid one with the reason, so a typo shows up as
   an error rather than a silently missing skill.

Skills can be toggled with `agent.skills_enabled` and relocated with
`agent.skills_dir` (Config → Agent). Note that some shipped skills (docx, pptx,
pdf) shell out to system tooling — `pandoc`, LibreOffice `soffice`, `pdftoppm`,
npm `docx` — which must be installed for those workflows to run.

## Tests

```bash
cd backend
PYTHONPATH=src .venv/bin/python -m pytest -q     # 98 tests
```

## Production notes

- Change `server.auth_token` before exposing beyond localhost.
- Run the backend behind a TLS-terminating proxy if remote.
- `npm run build` produces a static bundle in `frontend/dist/` servable by any
  static host; point it at the backend's public URL.
