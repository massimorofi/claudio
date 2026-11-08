# Claudio

A local-first agentic coding assistant: a chat surface that drives real tools in a
workspace, with human approval on destructive actions, budget tracking, context
compaction, and an autonomous maintenance daemon.

Claudio is **one long-lived agent** with **two independent client surfaces** (Web UI, VS Code).
The agent persists; clients come and go. See [doc/architecture.md](./doc/architecture.md).

---

## Quick Start

```bash
# Prerequisites: Python 3.11+, Node 20+, OpenAI-compatible endpoint

# 1. Configure LLM
cp claudio-backend/config/config.example.yaml claudio-backend/config/config.yaml
# Edit: llm.base_url, llm.model, llm.api_key

# 2. Start backend (daemon)
./backend-start.sh    # http://127.0.0.1:8100

# 3. Start frontend (separate terminal)
cd claudio-frontend && npm run dev    # http://localhost:5173
```

**Docker alternative**: See [doc/docker.md](./doc/docker.md).

---

## Components

| Component | Package | Purpose |
|-----------|---------|---------|
| **Core** | `claudio-core` | Framework-free: tools, storage, config, safety, event protocol |
| **Backend** | `claudio-backend` | ADK engine + FastAPI + autonomous daemon |
| **Frontend** | `claudio-frontend` | React SPA (chat, config, workspace, monitor, tasks, memory) |
| **VS Code** | `claudio-vscode` | Extension (chat + diffs + status bar) — *planned* |

Architecture guarantees: [doc/architecture.md](./doc/architecture.md)

---

## Documentation Map

| Doc | What it covers |
|-----|----------------|
| [architecture.md](./doc/architecture.md) | One agent, two clients — system narrative, guarantees, invariants |
| [backend.md](./doc/backend.md) | Engine internals, RunManager, daemon modules, ADK integration, API summary |
| [frontend.md](./doc/frontend.md) | Views, SSE handling, components, build |
| [core.md](./doc/core.md) | Event protocol, engine contract, tools, storage, safety, compaction |
| [configuration.md](./doc/configuration.md) | Single source for all config (env, yaml, secrets, restart semantics) |
| [skills.md](./doc/skills.md) | Skills directory, workspace paths, refresh workflow, authoring |
| [docker.md](./doc/docker.md) | Docker operational runbook |
| [archive/](./doc/archive/) | Historical specs and external analyses (reference only) |

---

## Configuration

All config lives in `claudio-backend/config/config.yaml` (gitignored).
Environment overrides: `CLAUDIO__SECTION__KEY=value`

| Section | Key Settings | Doc |
|---------|--------------|-----|
| `server` | `host`, `port`, `auth_token` | [configuration.md](./doc/configuration.md) |
| `llm` | `provider`, `model`, `base_url`, `api_key` | [configuration.md](./doc/configuration.md) |
| `agent` | `workspace_dir`, `approval_mode`, `max_turns`, `skills_dir` | [configuration.md](./doc/configuration.md) |
| `mcp` | External tool servers | [backend.md](./doc/backend.md#mcp-servers) |
| `autonomous` | Health, maintenance, recovery | [backend.md](./doc/backend.md#autonomous-daemon) |

**Critical gotcha**: `skills_dir` is cwd-independent; `workspace_dir` is cwd-dependent.
See [doc/skills.md](./doc/skills.md#3-the-resolution-asymmetry-gotcha).

---

## Skills

Curated Agent Skills in `claudio-backend/skills/` — merged per core area.
Refresh workflow: [doc/skills.md](./doc/skills.md#refreshing-skills)

---

## Development

```bash
# Core
cd claudio-core && .venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest

# Backend
cd claudio-backend && .venv/bin/pip install -e ".[dev]" && .venv/bin/lint-imports

# Frontend
cd claudio-frontend && npm install && npm run build
```

Verification checklist: [doc/architecture.md#6-invariants](./doc/architecture.md#6-invariants)
