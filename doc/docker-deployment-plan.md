# Docker Deployment Plan — Claudio (agent backend + web UI)

**Status:** Draft for approval
**Date:** 2026-10-27
**Target:** Two separate Docker containers (agent/backend, web UI) orchestrated with Docker Compose, plus one control script for `status` / `start` / `stop` of each container individually and of the stack as a whole.

---

## 1. Objective

Replace the current host-native process model (`backend-start.sh` + `nohup uvicorn` + `npm run dev`)
with a containerised model where:

1. **`claudio-agent`** — the Python backend (FastAPI/uvicorn + ADK agent runtime) runs in its own container.
2. **`claudio-web`** — the built React SPA served by nginx runs in a second container and reverse-proxies `/api` to the agent.
3. `docker compose up -d` brings the whole stack up; `docker compose down` takes it down.
4. A single script `./claudio-ctl` wraps everything: `status`, `start`, `stop`, `restart`, `logs`,
   per-service (`agent` / `web`) and for all services at once.

Non-goals (explicitly out of scope for this plan): Kubernetes, TLS termination, multi-host
orchestration, CI/CD publishing, and removing the existing `.sh`/`.bat` host scripts (they stay
working during migration).

---

## 2. Current state (verified in this repo)

| Fact | Value | Source |
|---|---|---|
| Backend app object | `claudio_backend.app:app` | `claudio-backend/src/claudio_backend/app.py` |
| Backend framework | FastAPI + uvicorn | `claudio-backend/pyproject.toml` |
| Default bind | `127.0.0.1:8100` | `config/config.yaml` → `server.host/port` |
| Liveness probe | `GET /healthz` (no auth) | `app.py` root route |
| Readiness probe | `GET /api/v1/readyz` (**auth required**) | `api/system.py` |
| Version | `GET /api/v1/version` | `api/system.py` |
| Agent runtime control | `POST /api/v1/agent/start\|stop\|restart`, `GET /api/v1/agent/status` | `api/management.py` |
| Config file | `claudio-backend/config/config.yaml` (gitignored, holds secrets) | `.gitignore` |
| Config written at runtime | Yes — `write_config_atomic()` on every config save | `api/config.py` |
| Env override mechanism | `CLAUDIO__<SECTION>__<KEY>` (exactly 2 parts, JSON-parsed) | `claudio_core/settings.py` |
| Runtime data | `claudio-backend/data/` — `claudio.db`, `logs/`, `sessions/`, `workspaces/`, `file-history/`, `skill-backups/` | live dir listing |
| Skills dir | `agent.skills_dir: ./skills` (relative → anchored to backend base dir) | `paths.py` |
| Workspace dir | `agent.default_workspace_dir` = absolute host path today | `config.yaml` |
| LLM endpoint | `http://192.168.1.172:8731/v1` (LAN, not localhost) | `config.yaml` |
| MCP server | `https://api.skillmd.com/mcp` (needs outbound internet) | `config.yaml` |
| Frontend | Vite 5 + React 18 + TS, dev port 5173, proxies `/api` → `127.0.0.1:8100` | `vite.config.ts` |
| Frontend API base | **relative** `/api/v1` (no hardcoded host) | `src/api/client.ts` |
| Streaming | POST-based SSE (`@microsoft/fetch-event-source`) | `src/api/sse.ts` |
| Python in venv | 3.11.16 (`requires-python >= 3.11`) | `.venv/bin/python` |
| Node | v22.22.1 | `node --version` |
| Docker | 29.9.0 / Compose v5.6.0 | installed |
| Existing Docker files | **None** | `find . -iname "*docker*"` |

Two facts drive most of the design:

- **The backend rewrites its own config file at runtime.** So `config/` cannot be a read-only
  baked-in file — it must live on a writable volume.
- **The frontend calls `/api/v1` relatively and streams SSE.** So the web container must be a
  real reverse proxy with buffering disabled and long read timeouts, not just a static file server.
---

## 3. Target architecture

```
                    host : 8080 (published)
                          │
              ┌───────────▼────────────┐
              │   claudio-web          │  nginx:1.27-alpine
              │   static SPA + proxy   │  no secrets, tiny image
              │                        │
              │  /            → dist/  │
              │  /api/*  ───┐ proxy    │
              │  /healthz ──┘ → agent  │
              └──────────┬─────────────┘
                       │  claudio-net (internal bridge)
                       │  http://claudio-agent:8100
              ┌──────────▼─────────────┐
              │  claudio-agent         │  python:3.12-slim
              │  uvicorn + ADK        │  + claudio-core, claudio-backend
              │                      │
              │  binds 0.0.0.0:8100  │
              └───┬──────────┬───────┘
                  │          │
     volumes      │          │  outbound: LLM (LAN), MCP (internet)
   ┌──────────────┴───┐    ┌┴──────────────────┐
   │ claudio-data     │    │ claudio-workspace   │
   │ (db, logs,       │    │ (user's repo the    │
   │  sessions,       │    │  agent edits)       │
   │  file-history)   │    └───────────────────┘
   │ claudio-config   │
   │ (config.yaml)    │
   │ claudio-skills   │
   └──────────────────┘
```

**Why exactly two containers (not one, not three):**

- One container per deployable keeps failure domains separate: an OOM in the Python agent
  (long turns, big context) never takes down the UI, and an nginx reload never interrupts a run.
- Image weight is separated: the web image is ~25 MB and rebuilds in seconds; the agent image
  is ~800 MB and rebuilds rarely.
- A third container (Postgres/Redis) is deliberately **not** introduced — Claudio uses SQLite,
  which is fine for a single-node deployment. Moving to Postgres is a separate future plan.
---

## 4. Decisions and assumptions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Agent binds `0.0.0.0:8100` inside the container.** | `127.0.0.1` inside a container is that container's own loopback; nginx could never reach it. Override with `CLAUDIO__SERVER__HOST=0.0.0.0`. |
| D2 | **Only nginx publishes a host port (8080).** The agent publishes nothing by default. | The agent holds the LLM key and runs shell tools; it must not be LAN-reachable by default. |
| D3 | **`config/`, `data/`, `skills/` become named volumes**, seeded on first run from a committed `config/config.example.yaml`. | The backend rewrites `config.yaml` at runtime; DB and transcripts must survive container replacement. |
| D4 | **Secrets come from a host `.env` file**, injected as `CLAUDIO__*` env vars, never baked into the image. | `.env` is already gitignored; env beats YAML in the loader, so it is the authoritative override. |
| D5 | **The agent's workspace is a bind mount of a host directory**, not an anonymous volume. | The point of Claudio is editing real repos; the user must see the files on the host. |
| D6 | **nginx serves the production build (`npm run build`), not `vite dev`.** | Vite's dev server is not a production server; `dist/` is static and needs no Node runtime. |
| D7 | **Compose project name pinned** (`name: claudio`) so container names are stable. | `claudio-agent-1` / `claudio-web-1` regardless of checkout path. |
| D8 | **Healthchecks use `/healthz` (unauthenticated)**, not `/readyz`. | `/readyz` requires a bearer token; putting a secret in a healthcheck buys nothing. Readiness is checked by the control script instead. |
| A1 | Single-node, single-user deployment; SQLite remains the store. | Matches current usage. |
| A2 | The LLM at `192.168.1.172:8731` and `api.skillmd.com` are reachable from the Docker bridge (default NAT). | Must be re-checked in Phase 6. |
| A3 | Linux host with Docker Engine >= 24 (observed 29.9.0). | All Compose features used are baseline. |

---

## 5. Phase 1 — Repo hygiene before any build (~20 min)

The build context currently contains ~470 MB of `.venv`, `node_modules` and runtime data.
Without a `.dockerignore` every build copies all of it, is slow, and leaks secrets into layers.

**Task 1.1 — Create `.dockerignore` at the repo root:**

```
.git
.gitignore
**/__pycache__/
**/*.py[cod]
**/.venv/
**/venv/
**/node_modules/
**/dist/
**/.pytest_cache/
**/.mypy_cache/
**/.ruff_cache/
**/.import_linter_cache/
**/*.tsbuildinfo
**/.vite/

# Runtime state and secrets must never enter an image
claudio-backend/data/
/data/
.claudio/
**/*.db
**/*.db-wal
**/*.db-shm
**/*.log
**/*.pid

# Local config & secrets
claudio-backend/config/config.yaml
.env
.env.*
!.env.example

# Host-only scratch
*.bat
temp/
todo/
doc/
```

**Task 1.2 — Commit a config template.** `config/config.yaml` is gitignored, so a fresh clone has
none. Create `claudio-backend/config/config.example.yaml` = the current `config.yaml` with every
secret replaced by a placeholder, and have the entrypoint copy it into the volume only when absent.

**Task 1.3 — Create `.env.example` at the repo root:**

```
# --- published ports ---
CLAUDIO_HTTP_PORT=8080              # web UI on the host
# --- agent server ---
CLAUDIO__SERVER__HOST=0.0.0.0
CLAUDIO__SERVER__PORT=8100
CLAUDIO__SERVER__AUTH_TOKEN=change-me-to-a-real-token
CLAUDIO__SERVER__CORS_ORIGINS=["http://localhost:8080"]
# --- LLM ---
CLAUDIO__LLM__BASE_URL=http://192.168.1.172:8731/v1
CLAUDIO__LLM__API_KEY=local
CLAUDIO__LLM__MODEL=halogen-qwen3.8-flash-next
# --- search providers (optional) ---
CLAUDIO__SEARCH__BRAVE_API_KEY=
# --- host dir the agent may edit ---
CLAUDIO_WORKSPACE_DIR=./workspace
```

> **Caveat found while reading the loader:** `_env_overrides()` accepts only keys with **exactly
> two** parts (`SECTION__KEY`). `CLAUDIO__SERVER__CORS_ORIGINS` is valid (section `server`,
> key `cors_origins`); there is **no** three-level override, so nested values such as
> `agent.tools.enabled` cannot be set from the environment — they must come from the YAML in
> the config volume.
---

## 6. Phase 2 — The agent container (`claudio-agent`)

**Task 2.1 — `docker/backend/Dockerfile`**

Base is `python:3.11-slim` deliberately: the host venv that everything was tested against is
3.11.16, and `requires-python >= 3.11` allows nothing older. Matching the interpreter exactly
avoids discovering an ADK/`pydantic` wheel problem at the same time as a Docker problem.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git/curl: the agent's run_command and web_fetch tools assume a normal shell
# environment. ca-certificates is required for https MCP + LLM calls.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user. /app is NOT the repo root: the app is installed
# into site-packages, so the image carries no writable source tree.
RUN useradd --create-home --uid 10001 claudio

WORKDIR /srv/claudio

# Install deps first so this layer is cached across source edits.
COPY claudio-core/pyproject.toml /build/claudio-core/
COPY claudio-core/src /build/claudio-core/src
COPY claudio-backend/pyproject.toml /build/claudio-backend/
COPY claudio-backend/src /build/claudio-backend/src
RUN pip install --no-cache-dir /build/claudio-core \
    && pip install --no-cache-dir /build/claudio-backend

# Writable state the container owns (mounted as volumes at runtime).
RUN mkdir -p /srv/claudio/config /srv/claudio/data /srv/claudio/skills \
    && chown -R claudio:claudio /srv/claudio

COPY docker/backend/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER claudio
EXPOSE 8100

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8100/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

**Task 2.2 — `docker/backend/entrypoint.sh`**

The entrypoint solves the chicken-and-egg of D3: the config volume starts empty on a fresh
install, but the app refuses to be useful without a config file.

```bash
#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR=/srv/claudio/config
SEED=/build/claudio-backend/config/config.example.yaml

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  echo "[entrypoint] no config.yaml in volume, seeding from example"
  cp "$SEED" "$CONFIG_DIR/config.yaml"
fi

# The app resolves CONFIG_PATH relative to the installed package's parents[2].
# Point it at the volume by running from the mounted location.
exec uvicorn claudio_backend.app:app \
  --host "${CLAUDIO__SERVER__HOST:-0.0.0.0}" \
  --port "${CLAUDIO__SERVER__PORT:-8100}" \
  --log-level warning \
  --proxy-headers \
  --forwarded-allow-ips '*'
```

**Task 2.3 — verify the config path assumption.** `app.py` computes
`BASE_DIR = Path(__file__).resolve().parents[2]` and `CONFIG_PATH = BASE_DIR/"config"/"config.yaml"`.
Once installed into site-packages, `parents[2]` is **not** `/srv/claudio` — it is the
site-packages directory. This must be checked in Phase 6 and, if it does not land on the volume,
resolved by one of:
  - (a) running uvicorn with the app installed in editable mode from `/srv/claudio`
    (`pip install -e`), so `__file__` really is under the mount; or
  - (b) a small code change to honour a `CLAUDIO_CONFIG_PATH` env var.

Option (a) is preferred for this step because it needs no code change; option (b) is the
correct long-term fix and is listed in §11 as follow-up work.
---

## 7. Phase 3 — The web UI container (`claudio-web`)

Two-stage build: Node builds the static bundle, nginx serves it and proxies the API.

**Task 3.1 — `docker/web/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1

# ---- build stage -------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY claudio-frontend/package.json claudio-frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY claudio-frontend/ ./
RUN npm run build          # -> /app/dist

# ---- runtime stage -----------------------------------------------------------
FROM nginx:1.27-alpine
COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
```

**Task 3.2 — `docker/web/nginx.conf`**

The SSE requirements are the part most likely to be gotten wrong. Claudio streams agent output
over a **POST** SSE connection, so nginx must not buffer the response, must not time the read
out after the default 60 s, and must stay on HTTP/1.1 (HTTP/1.0 breaks chunked streaming).

```nginx
# Non-root nginx on a high port, so the container needs no CAP_NET_BIND_SERVICE.
pid  /tmp/nginx.pid;
error_log /var/log/nginx/error.log warn;

events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    access_log    /var/log/nginx/access.log;
    sendfile      on;
    gzip          on;
    gzip_types    text/plain text/css application/json application/javascript;

    upstream claudio_agent {
        server claudio-agent:8100;
        keepalive 16;
    }

    server {
        listen 8080;
        server_name _;

        root  /usr/share/nginx/html;
        index index.html;

        # --- API -> agent ----------------------------------------------------
        location /api/ {
            proxy_pass         http://claudio_agent;
            proxy_http_version 1.1;

            proxy_set_header   Host              $host;
            proxy_set_header   X-Real-IP         $remote_addr;
            proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header   X-Forwarded-Proto $scheme;

            # SSE: never buffer, never let the proxy cut the stream.
            proxy_buffering    off;
            proxy_cache        off;
            proxy_request_buffering off;
            proxy_set_header   Connection       "";
            proxy_set_header   X-Accel-Buffering no;

            # Long turns: the agent can be silent between tool calls.
            proxy_connect_timeout 10s;
            proxy_send_timeout    3600s;
            proxy_read_timeout    3600s;

            client_max_body_size 50m;
        }

        # Direct liveness passthrough for external probes.
        location = /healthz {
            proxy_pass http://claudio_agent/healthz;
            proxy_http_version 1.1;
            proxy_buffering off;
        }

        # --- SPA -------------------------------------------------------------
        location ~* \.(?:js|css|svg|png|jpg|jpeg|gif|ico|woff2?)$ {
            expires 7d;
            add_header Cache-Control "public, immutable";
            try_files $uri =404;
        }

        location / {
            try_files $uri $uri/ /index.html;
            add_header Cache-Control "no-cache";
        }
    }
}
```

**Task 3.3 — CORS.** Today `server.cors_origins` lists `http://localhost:5173`. Under the
container topology the browser origin becomes `http://localhost:8080` (or the LAN hostname).
Because nginx proxies `/api` from the same origin, CORS is effectively same-origin and mostly
moot — but update the value anyway so direct-to-agent debugging works.
---

## 8. Phase 4 — Docker Compose (`compose.yaml`)

**Task 4.1 — `compose.yaml` at the repo root**

```yaml
name: claudio

x-common: &common
  restart: unless-stopped
  init: true
  logging:
    driver: json-file
    options:
      max-size: "20m"
      max-file: "5"

services:
  # ---------------------------------------------------------------- agent ----
  agent:
    <<: *common
    image: claudio/agent:${CLAUDIO_TAG:-latest}
    build:
      context: .
      dockerfile: docker/backend/Dockerfile
    container_name: claudio-agent
    environment:
      CLAUDIO__SERVER__HOST: "0.0.0.0"
      CLAUDIO__SERVER__PORT: "8100"
      CLAUDIO__SERVER__AUTH_TOKEN: ${CLAUDIO__SERVER__AUTH_TOKEN:?set in .env}
      CLAUDIO__SERVER__CORS_ORIGINS: ${CLAUDIO__SERVER__CORS_ORIGINS:-["http://localhost:8080"]}
      CLAUDIO__LLM__BASE_URL: ${CLAUDIO__LLM__BASE_URL:?set in .env}
      CLAUDIO__LLM__API_KEY: ${CLAUDIO__LLM__API_KEY:?set in .env}
      CLAUDIO__LLM__MODEL: ${CLAUDIO__LLM__MODEL:-halogen-qwen3.8-flash-next}
      CLAUDIO__SEARCH__BRAVE_API_KEY: ${CLAUDIO__SEARCH__BRAVE_API_KEY:-}
      TZ: ${TZ:-UTC}
    volumes:
      - claudio-config:/srv/claudio/config
      - claudio-data:/srv/claudio/data
      - claudio-skills:/srv/claudio/skills
      # The repo(s) the agent edits. Bind, not named volume, so the user sees them.
      - ${CLAUDIO_WORKSPACE_DIR:-./workspace}:/workspace
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8100/healthz"]
      interval: 15s
      timeout: 5s
      start_period: 30s
      retries: 5
    networks: [claudio-net]
    # No published ports by default. For direct debugging, uncomment:
    # ports:
    #   - "127.0.0.1:8100:8100"

  # ------------------------------------------------------------------ web ----
  web:
    <<: *common
    image: claudio/web:${CLAUDIO_TAG:-latest}
    build:
      context: .
      dockerfile: docker/web/Dockerfile
    container_name: claudio-web
    depends_on:
      agent:
        condition: service_healthy
    ports:
      - "${CLAUDIO_HTTP_PORT:-8080}:8080"
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
      interval: 15s
      timeout: 5s
      start_period: 10s
      retries: 3
    networks: [claudio-net]

networks:
  claudio-net:
    driver: bridge

volumes:
  claudio-config:
  claudio-data:
  claudio-skills:
```

**Task 4.2 — profiles (optional but cheap).** Add `profiles: ["debug"]` to a third
ephemeral service (e.g. an `alpine` container with `curl`/`jq`) for network debugging
without exec-ing into the agent.

**Task 4.3 — resource caps.** The agent can be handed a long context and a fan-out of
subagents. Add `mem_limit` / `cpus` (or `deploy.resources.limits`) once Phase 6 shows the
steady-state footprint, so a runaway run cannot freeze the host.
---

## 9. Phase 5 — Control script (`./claudio-ctl`)

One script, three verbs the user asked for (`status`, `start`, `stop`) plus the obvious
neighbours, each accepting **a single service or no argument (= all services)**.

**Task 5.1 — `claudio-ctl` at the repo root**

```bash
#!/usr/bin/env bash
# claudio-ctl — start/stop/status for the Claudio docker stack.
#
#   ./claudio-ctl status [agent|web|all]     default: all
#   ./claudio-ctl start  [agent|web|all]     default: all
#   ./claudio-ctl stop   [agent|web|all]     default: all
#   ./claudio-ctl restart [agent|web|all]    default: all
#   ./claudio-ctl logs   [agent|web|all] [-f]
#   ./claudio-ctl build  [agent|web|all]
#   ./claudio-ctl health                     probe /healthz + /api/v1/agent/status
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

COMPOSE="docker compose"
ALL_SERVICES=(agent web)
AGENT_PORT="${CLAUDIO__SERVER__PORT:-8100}"
WEB_PORT="${CLAUDIO_HTTP_PORT:-8080}"
TOKEN="${CLAUDIO__SERVER__AUTH_TOKEN:-dev-token-change-me}"

# Load .env so bare ./claudio-ctl status sees the same ports/tokens as compose.
[ -f .env ] && set -a && . ./.env && set +a

c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_off=$'\033[0m'

die() { echo "${c_bad}$*${c_off}" >&2; exit 1; }

# Normalise the argument list into SERVICES. "all" expands; unknown names die.
resolve_services() {
  local args=("$@") out=()
  if [ ${#args[@]} -eq 0 ]; then out=("${ALL_SERVICES[@]}"); else
    for a in "${args[@]}"; do
      case "$a" in
        all) out+=("${ALL_SERVICES[@]}") ;;
        agent|web) out+=("$a") ;;
        *) die "unknown service '$a' (expected: agent | web | all)" ;;
      esac
    done
  fi
  printf '%s\n' "${out[@]}"
}

require_docker() {
  docker info >/dev/null 2>&1 || die "docker daemon is not running / not accessible"
}

# --- status ------------------------------------------------------------------
svc_status() {
  local svc="$1"
  local cid state health
  cid=$($COMPOSE ps -q "$svc" 2>/dev/null)
  if [ -z "$cid" ]; then
    printf '  %-6s %s\n' "$svc" "${c_bad}not created${c_off}"; return 1
  fi
  state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo unknown)
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo unknown)
  case "$health" in
    healthy)   printf '  %-6s %s %s\n' "$svc" "${c_ok}$state${c_off}" "(health: $health)" ;;
    unhealthy) printf '  %-6s %s %s\n' "$svc" "${c_bad}$state${c_off}" "(health: $health)" ;;
    *)         printf '  %-6s %s %s\n' "$svc" "${c_warn}$state${c_off}" "(health: $health)" ;;
  esac
  [ "$state" = "running" ]
}

cmd_status() {
  local rc=0
  echo "Claudio stack — status"
  while read -r s; do svc_status "$s" || rc=1; done < <(resolve_services "$@")
  health_only || rc=1
  return $rc
}

# Deep health: HTTP-level, not just container-level.
health_only() {
  local ok=0
  if curl -fsS -m 3 "http://127.0.0.1:${WEB_PORT}/healthz" >/dev/null 2>&1; then
    echo "  web->agent  ${c_ok}reachable${c_off}"
  else
    echo "  web->agent  ${c_bad}unreachable${c_off}"; ok=1
  fi
  local st
  st=$(curl -fsS -m 3 -H "Authorization: Bearer $TOKEN" \
        "http://127.0.0.1:${WEB_PORT}/api/v1/agent/status" 2>/dev/null)
  if [ -n "$st" ]; then
    echo "  agent run   ${c_ok}$st${c_off}"
  else
    echo "  agent run   ${c_warn}no response${c_off}"; ok=1
  fi
  return $ok
}
```

*(The script continues — the remaining verbs are specified below and are written into the
same file in Phase 5.)*
**Task 5.2 — start / stop semantics**

Ordering matters and is asymmetric:
- **start**: agent first, then web (compose's `depends_on: condition: service_healthy` does this).
- **stop**: web first, then agent — killing the agent while clients are still routed to it
  produces a visible error storm; the reverse is silent.

```bash
cmd_start() {
  require_docker
  local services; services="$(resolve_services "$@")"
  if [ "$services" = "$(printf '%s\n' "${ALL_SERVICES[@]}")" ]; then
    echo "Starting full stack ..."
    $COMPOSE up -d "$@" || die "compose up failed"
  else
    # Bring up dependencies explicitly so "start web" does not fail on a dead agent.
    $COMPOSE up -d --no-build $services || die "compose up failed for: $services"
  fi
  echo "Waiting for health ..."
  wait_healthy 60
}

cmd_stop() {
  require_docker
  local services; services="$(resolve_services "$@")"
  # Reverse order: web before agent.
  local ordered=()
  for s in web agent; do
    for want in $services; do [ "$s" = "$want" ] && ordered+=("$s"); done
  done
  echo "Stopping: ${ordered[*]}"
  docker stop -t 20 $(printf '%s\n' "${ordered[@]}" | xargs -I{} $COMPOSE ps -q {}) 2>/dev/null \
    || $COMPOSE stop "${ordered[@]}" || die "compose stop failed"
  echo "Stopped."
}

wait_healthy() {
  local timeout="${1:-60}" waited=0
  while [ "$waited" -lt "$timeout" ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${WEB_PORT}/healthz" >/dev/null 2>&1; then
      echo "${c_ok}Stack healthy after ${waited}s${c_off}"; return 0
    fi
    sleep 2; waited=$((waited + 2))
  done
  echo "${c_bad}Stack not healthy after ${timeout}s${c_off}"
  echo "Recent agent logs:"; $COMPOSE logs --tail 30 agent
  return 1
}

cmd_restart() { cmd_stop "$@"; cmd_start "$@"; }

cmd_logs() {
  local follow="" svc_args=()
  for a in "$@"; do [ "$a" = "-f" ] && follow="-f" || svc_args+=("$a"); done
  [ ${#svc_args[@]} -eq 0 ] && svc_args=("${ALL_SERVICES[@]}")
  $COMPOSE logs $follow --tail 200 "${svc_args[@]}"
}

cmd_build() {
  local services; services="$(resolve_services "$@")"
  $COMPOSE build $services
}

cmd_down() { require_docker; $COMPOSE down "${@:+$@}"; }

# --- dispatch ---------------------------------------------------------------
ACTION="${1:-status}"; shift || true
case "$ACTION" in
  status)  cmd_status "$@" ;;
  start)   cmd_start "$@" ;;
  stop)    cmd_stop "$@" ;;
  restart) cmd_restart "$@" ;;
  logs)    cmd_logs "$@" ;;
  build)   cmd_build "$@" ;;
  down)    cmd_down "$@" ;;
  health)  health_only ;;
  *)       die "unknown action '$ACTION'. Use: status|start|stop|restart|logs|build|down|health" ;;
esac
```

**Task 5.3 — exit codes.** `status` exits non-zero if any service is not running or the
HTTP probe fails, so the script is usable from cron/CI:

| Code | Meaning |
|---|---|
| 0 | everything requested is up and healthy |
| 1 | a service is down, unhealthy, or the HTTP probe failed |
| 2 | bad usage (unknown service or action) |

**Task 5.4 — `chmod +x claudio-ctl`** and add a one-line usage banner printed on
`./claudio-ctl` with no arguments.

**Task 5.5 — Windows parity.** The repo already carries `.bat` twins for every shell script,
so add `claudio-ctl.bat` delegating to the same compose file (`docker compose up -d`,
`docker compose ps`, `docker compose stop`) to keep the Windows workflow working.
---

## 10. Phase 6 — Verification checklist

Run in order; each step is a pass/fail gate before the next.

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Context size sane | `docker build --no-cache -f docker/backend/Dockerfile -t t . --progress=plain 2>&1 \| head` | no `.venv`/`node_modules`/`data` copied |
| 2 | Images build | `docker compose build` | both images built |
| 3 | Stack up | `./claudio-ctl start` | both containers `running` + `healthy` |
| 4 | Agent live | `curl -s localhost:8080/healthz` | `{"status":"ok"}` |
| 5 | Proxy works | `curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/api/v1/version` | JSON with backend version |
| 6 | Config volume writable | `docker compose exec agent sh -c 'touch /srv/claudio/config/.w && rm $_'` | success |
| 7 | Config path correct | `docker compose exec agent python -c "from claudio_backend.app import CONFIG_PATH; print(CONFIG_PATH, CONFIG_PATH.exists())"` | points at the mounted file, `True` |
| 8 | LLM reachable from container | `docker compose exec agent curl -sS $LLM_BASE/models` | 200 + model list |
| 9 | MCP reachable | `docker compose exec agent curl -sSI https://api.skillmd.com/mcp` | reachable |
| 10 | SSE streams through nginx | `curl -N -X POST localhost:8080/api/v1/chat/stream -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"message":"say hi"}'` | events arrive incrementally, not all at the end |
| 11 | Per-service stop | `./claudio-ctl stop web` then `status` | web down, agent still running |
| 12 | Per-service start | `./claudio-ctl start web` | web healthy again |
| 13 | Data survives restart | `./claudio-ctl restart` → list sessions in UI | previous sessions present |
| 14 | Workspace visible | create a file in the UI workspace, check it on the host | same file both sides |
| 15 | Restart policy | `docker kill claudio-agent` | container comes back (`unless-stopped`) |
| 16 | Clean shutdown | `./claudio-ctl stop all` | no orphan processes, no `*.db-wal` left growing |

**Task 6.1 — a smoke script.** Wrap checks 3–10 in `docker/smoke_test.sh` so the same
gate can be run after every rebuild.

---

## 11. Rollback plan

The migration is reversible at every step because the host scripts are not deleted.

| Level | Symptom | Rollback |
|---|---|---|
| L1 — one service | web broken, agent fine | `./claudio-ctl restart web`; if the image is bad, `CLAUDIO_TAG=previous ./claudio-ctl start web` |
| L2 — whole stack | stack will not come up | `./claudio-ctl stop all` then `./backend-start.sh` + `cd claudio-frontend && npm run dev` (the old path, untouched) |
| L3 — bad config in volume | config written by the new setup is wrong | `docker volume rm claudio_config` → entrypoint reseeds from `config.example.yaml` |
| L4 — data corruption | DB unreadable | restore `claudio-backend/data/claudio.db` from the pre-migration backup (a `.bak` already exists today) |

**Tag discipline:** build with an immutable tag (`CLAUDIO_TAG=$(git rev-parse --short HEAD)`)
as well as `latest`, so "roll back one image" is a one-line change rather than a rebuild.

**Pre-migration backup (do this before Phase 4 the first time):**

```bash
tar --exclude='*/node_modules' --exclude='*/.venv' -czf \
    ~/claudio-pre-docker-backup-$(date +%F).tar.gz \
    claudio-backend/data claudio-backend/config
```

---

## 12. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `CONFIG_PATH` resolves into site-packages, not the volume (D3/Task 2.3) | **High** | Config edits via UI appear to vanish | Verify in check #7 before anything else; fall back to editable install or add `CLAUDIO_CONFIG_PATH` support |
| SSE buffered by nginx → UI looks frozen | Medium | High | `proxy_buffering off` + `X-Accel-Buffering: no` + HTTP/1.1; verify with check #10 |
| SQLite file locking across the container boundary | **Low on this host** | High if it bites | Verified: `/run/media/briggen/DEV` is **ext4**, so POSIX locking works and SQLite is safe on either a bind mount or a named volume. Named volumes are still used for `claudio-data` (D3) because they are independent of the checkout path and of host permission changes. Re-check `df -T` if the repo is ever moved to a non-Linux-native filesystem. |
| File ownership mismatch (uid 10001 vs host user) on bind mounts | Medium | Medium | `user: "10001:1000"` in compose matching the host group, or an init chown in the entrypoint |
| LLM is a LAN IP → breaks if the host network changes | Medium | High | Put the LLM in its own compose service or make the base_url an env var (already planned); re-test after any network change |
| Agent container needs broad file access (it runs shell commands) | Certain | High | Treat the agent container as **trusted-but-bounded**: mount only the specific workspace, run unprivileged, publish no ports, keep `workspace_allowed_roots` set in config |
| Image bloat / slow rebuild from ADK + litellm | Medium | Low | Multi-stage build, `--no-cache-dir`, pin the base digest |
| Two containers drift from host dev workflow | Medium | Medium | Keep `backend-start.sh` working; document both paths in README |

---

## 13. Follow-up work (explicitly not in this plan)

1. **`CLAUDIO_CONFIG_PATH` env override** — removes the fragile `parents[2]` assumption (Task 2.3).
2. **Postgres instead of SQLite** — prerequisite for more than one agent replica.
3. **TLS** — Caddy or Traefik with Let's Encrypt in front of `claudio-web`.
4. **Auth in front of the whole stack** — today the token is the only gate.
5. **CI pipeline** — build + push images, run `docker/smoke_test.sh` on every push.
6. **Resource limits** — set `mem_limit`/`cpus` from the steady-state numbers measured in Phase 6.
7. **Backup job** for the `claudio-data` volume (the autonomous module already has a backup concept — wire it to the volume).

---

## 14. Deliverables produced by this plan

| File | Purpose |
|---|---|
| `.dockerignore` | keeps junk and secrets out of the build context |
| `.env` / `.env.example` | host-side configuration and secrets |
| `docker/backend/Dockerfile` | the agent image |
| `docker/backend/entrypoint.sh` | seeds config volume, launches uvicorn |
| `docker/web/Dockerfile` | two-stage build of the SPA into nginx |
| `docker/web/nginx.conf` | static serving + SSE-safe API proxy |
| `compose.yaml` | the two-service stack, networks, volumes, healthchecks |
| `claudio-ctl` (+ `.bat`) | `status` / `start` / `stop` / `restart` / `logs` per service and for all |
| `docker/smoke_test.sh` | post-build verification gate |
| `claudio-backend/config/config.example.yaml` | committed, secret-free config template |
