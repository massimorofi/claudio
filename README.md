# Claudio

A local-first agentic coding assistant: a chat surface that drives real tools in a
workspace, with human approval on destructive actions, budget tracking, context
compaction, and an autonomous maintenance daemon.

Claudio is **one long-lived agent** with **two independent client surfaces** (Web UI, VS Code).
The agent persists; clients come and go. See [doc/architecture.md](./doc/architecture.md).

The agent's "brain" is pluggable: **ADK** (default, in-process), **OpenCode** or
**Claude Code** — one engine per backend process, identical API/UI underneath.
See [doc/engines.md](./doc/engines.md).

![image](doc/Claudio_Components_Architecture.jpeg)

---

## Quick Start

Pick the path that matches your setup. All paths end with the same Web UI and
the same API; only the engine and the runtime differ.

**Prerequisites:** Python 3.11+ and Node 20+ (paths A), or Docker with Compose v2
(paths B–D), plus a reachable model endpoint.

### A. Plain local run — ADK engine (default)

Any **OpenAI-compatible** endpoint (LM Studio, vLLM, llama.cpp, OpenAI…).

```bash
# 1. Configure LLM
cp claudio-backend/config/config.example.yaml claudio-backend/config/config.yaml
```

Edit `claudio-backend/config/config.yaml` — the minimum:

```yaml
llm:
  base_url: http://127.0.0.1:1234/v1   # your OpenAI-compatible server
  api_key: local                        # or your real key
  model: qwen3.5-4b                     # model id served by that endpoint
server:
  auth_token: change-me-to-a-long-random-token
```

```bash
# 2. Start backend (daemon) — separate terminal
./backend-start.sh                      # http://127.0.0.1:8100

# 3. Start frontend — separate terminal
cd claudio-frontend && npm install && npm run dev   # http://localhost:5173
```

Verify (should report `"engine": "adk"`):

```bash
curl -s -H "Authorization: Bearer change-me-to-a-long-random-token" \
  http://127.0.0.1:8100/api/v1/version
```

### B. Docker — ADK engine

```bash
cp .env.example .env    # edit: CLAUDIO__SERVER__AUTH_TOKEN, CLAUDIO__LLM__*, CLAUDIO_WORKSPACE_DIR
./claudio-ctl start     # builds if needed, waits for health → http://localhost:8080
./docker/smoke_test.sh  # 12 checks
```

Runbook (host execution, volumes, gotchas): [doc/docker.md](./doc/docker.md).

### C. OpenCode engine (Docker)

OpenCode drives any **OpenAI-compatible** endpoint — the same one Claudio
already uses.

**Automated (recommended):**

```bash
scripts/install-engine.sh opencode
# prompts: auth token, base URL, API key, model, pinned OpenCode CLI version
```

**Manual** — put this in `.env` (gitignored):

```bash
# shared backend
CLAUDIO__SERVER__AUTH_TOKEN=<a-long-random-token>
CLAUDIO_HTTP_PORT=8080

# engine selection + model endpoint
CLAUDIO_ENGINE=opencode
CLAUDIO__LLM__BASE_URL=http://192.168.1.172:8731/v1
CLAUDIO__LLM__API_KEY=local
CLAUDIO__LLM__MODEL=qwen3.5-4b

# pin the CLI baked into the image (never 'latest'; placeholder = build fails)
OPENCODE_VERSION=0.6.5
```

Then build and start the OpenCode image variant:

```bash
docker compose -f compose.yaml -f compose.engine-opencode.yaml build agent
docker compose -f compose.yaml -f compose.engine-opencode.yaml up -d
```

Verify — expect `"engine": "opencode"`:

```bash
curl -s -H "Authorization: Bearer <token>" http://localhost:8080/api/v1/version
```

### D. Claude Code engine (Docker)

Claude Code speaks the **Anthropic Messages API** (`/v1/messages`) — it is *not*
automatically compatible with an OpenAI-only `/v1/chat/completions` server.
Point it at `api.anthropic.com` or at a **verified** conversion gateway; the
adapter refuses to start otherwise.

**Automated (recommended):**

```bash
scripts/install-engine.sh claude_code
# prompts: auth token, Anthropic base URL, ANTHROPIC_API_KEY, model,
#          pinned CLI version, endpoint-verified? (true only after checking /v1/messages)
```

**Manual** — put this in `.env`:

```bash
# shared backend
CLAUDIO__SERVER__AUTH_TOKEN=<a-long-random-token>
CLAUDIO_HTTP_PORT=8080

# engine selection + Anthropic Messages endpoint
CLAUDIO_ENGINE=claude_code
CLAUDIO__LLM__BASE_URL=https://api.anthropic.com/v1   # must serve /v1/messages
CLAUDIO__LLM__API_KEY=sk-ant-...
ANTHROPIC_API_KEY=sk-ant-...
CLAUDIO__LLM__MODEL=claude-sonnet-4-5

# pin the CLI baked into the image
CLAUDE_CODE_VERSION=2.0.14

# ONLY after verifying the endpoint serves /v1/messages (e.g. a conversion gateway):
CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT=false
```

Then build and start the Claude Code image variant:

```bash
docker compose -f compose.yaml -f compose.engine-claude-code.yaml build agent
docker compose -f compose.yaml -f compose.engine-claude-code.yaml up -d
```

Verify — expect `"engine": "claude_code"`:

```bash
curl -s -H "Authorization: Bearer <token>" http://localhost:8080/api/v1/version
```

### Switching engines / rollback

Switching recreates only the `agent` container (no frontend rebuild). Roll back
to ADK with `docker compose -f compose.yaml up -d --build agent` (no
`CLAUDIO_ENGINE` set). Full guide incl. troubleshooting:
[doc/engine-setup.md](./doc/engine-setup.md); architecture of the pluggable
engines: [doc/engines.md](./doc/engines.md).

---

## Components

| Component | Package | Purpose |
|-----------|---------|---------|
| **Core** | `claudio-core` | Framework-free: tools, storage, config, safety, event protocol, engine contract |
| **Backend** | `claudio-backend` | Pluggable engines (ADK / OpenCode / Claude Code) + FastAPI + autonomous daemon |
| **Frontend** | `claudio-frontend` | React SPA (chat, config, workspace, monitor, tasks, memory) |
| **VS Code** | `claudio-vscode` | Extension (chat + diffs + status bar) — *planned* |

Architecture guarantees: [doc/architecture.md](./doc/architecture.md)

---

## Documentation Map

| Doc | What it covers |
|-----|----------------|
| [architecture.md](./doc/architecture.md) | One agent, two clients — system narrative, run-lifecycle state diagram, guarantees, invariants |
| [engines.md](./doc/engines.md) | **Multi-backend architecture (issue #57)** — factory, selection, checkpoints, CLI adapters, Mermaid state diagrams |
| [engine-setup.md](./doc/engine-setup.md) | Operational: build/run the OpenCode & Claude Code containers, troubleshooting |
| [backend.md](./doc/backend.md) | ADK engine internals, RunManager, daemon modules, MCP, API summary |
| [frontend.md](./doc/frontend.md) | Views, SSE handling, components, build |
| [core.md](./doc/core.md) | Event protocol, engine contract, tools, storage, safety, compaction |
| [configuration.md](./doc/configuration.md) | Single source for all config (env, yaml, secrets, engine, restart semantics) |
| [skills.md](./doc/skills.md) | Skills directory, workspace paths, refresh workflow, authoring |
| [docker.md](./doc/docker.md) | Docker operational runbook |


---

## Configuration

All config lives in `claudio-backend/config/config.yaml` (gitignored).
Environment overrides: `CLAUDIO__SECTION__KEY=value`

| Section | Key Settings | Doc |
|---------|--------------|-----|
| `server` | `host`, `port`, `auth_token` | [configuration.md](./doc/configuration.md) |
| `llm` | `provider`, `model`, `base_url`, `api_key` | [configuration.md](./doc/configuration.md) |
| `agent` | `workspace_dir`, `approval_mode`, `max_turns`, `skills_dir` | [configuration.md](./doc/configuration.md) |
| `engine` | `kind` (`adk`/`opencode`/`claude_code`), timeouts, per-engine options | [engines.md](./doc/engines.md) |
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
