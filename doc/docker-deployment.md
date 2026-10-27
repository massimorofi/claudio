# Docker Deployment — Implementation Notes & Runbook

**Status:** Implemented and verified
**Date:** 2026-10-27
**Plan:** [`docker-deployment-plan.md`](./docker-deployment-plan.md)

The plan was implemented as specified, with four corrections where the plan's
assumptions did not survive contact with the code. Those corrections are in
§3 and are the parts worth reading twice.

---

## 1. What was built

| File | Purpose |
|---|---|
| `.dockerignore` | Keeps venvs, `node_modules`, live state and secrets out of the build context |
| `.env` | Live host-side config and secrets (gitignored) |
| `.env.example` | Committed template |
| `claudio-backend/config/config.example.yaml` | Committed, secret-free config template |
| `docker/backend/Dockerfile` | `claudio/agent` — FastAPI/uvicorn + ADK runtime |
| `docker/backend/entrypoint.sh` | Seeds the config volume, validates it, execs uvicorn |
| `docker/web/Dockerfile` | `claudio/web` — two-stage Node build → nginx |
| `docker/web/nginx.conf` | Static SPA + SSE-safe reverse proxy for `/api` |
| `compose.yaml` | Two-service stack, network, volumes, healthchecks |
| `claudio-ctl` | `status` / `start` / `stop` / `restart` / `logs` / `build` / `health` / `down` |
| `claudio-ctl.bat` | Windows twin |
| `docker/smoke_test.sh` | Post-build verification gate |

Topology: nginx publishes **8080** only. The agent publishes **nothing** to the
host and is reachable only over the internal `claudio-net` bridge.

---

## 2. Quick start

```bash
cp .env.example .env      # then edit: token, LLM URL, model, workspace path
./claudio-ctl start       # builds if needed, waits for health
./docker/smoke_test.sh    # 12 checks
```

Per-service control:

```bash
./claudio-ctl status web
./claudio-ctl stop web
./claudio-ctl restart agent
./claudio-ctl logs agent -f
```

Exit codes: `0` all good, `1` something down or a probe failed, `2` bad usage.

---

## 3. Corrections to the plan

### 3.1 Volume mount paths (was: silently broken)

The plan mounted the config volume at `/srv/claudio/config`. That is wrong.

Both `app.py` and `paths.py` derive the runtime base from
`Path(__file__).resolve().parents[2]`. For a `src/`-layout package that is the
**`claudio-backend` directory**, not the repo root. Verified on the host:

```
BASE_DIR    -> <repo>/claudio-backend
CONFIG_PATH -> <repo>/claudio-backend/config/config.yaml
```

So the volumes are mounted at the paths the app actually uses:

```
claudio-config  -> /srv/claudio/claudio-backend/config
claudio-data    -> /srv/claudio/claudio-backend/data
claudio-skills  -> /srv/claudio/claudio-backend/skills
```

The plan rated this risk "High" and told us to verify it first. We did
(smoke check #6), and it confirmed the corrected path.

### 3.2 `.env` quoting broke the whole stack (was: agent never starts)

The plan wrote:

```
CLAUDIO__SERVER__CORS_ORIGINS=["http://localhost:8080"]
```

`claudio-ctl` sources `.env` with `. ./.env`. Bash performs **quote removal**
on the right-hand side of an assignment, so the value reaching the process was
`[http://localhost:8080]` — not valid JSON. The config loader's
`json.loads()` therefore fell through to the raw string, pydantic rejected it
(`Input should be a valid list`), `load_config()` raised, and
`supervisor.config` stayed `None`. Every authenticated endpoint then returned
401 while the container still reported *healthy*, because `/healthz` is
unauthenticated.

Fix: single-quote JSON values.

```
CLAUDIO__SERVER__CORS_ORIGINS='["http://localhost:8080"]'
```

Single quotes survive both bash sourcing and compose's own `.env` parsing.
**Any JSON-valued key in `.env` must be single-quoted.**

### 3.3 `nginx -t` cannot run at build time

The plan validated the nginx config during the image build. nginx resolves
upstream hostnames at config-parse time, and `claudio-agent` does not exist
until the compose network does, so the build failed with
`host not found in upstream "claudio-agent:8100"`.

Removed from the build. nginx validates its own config on every start, and the
smoke test asserts the proxy works, so nothing is unvalidated in practice.

### 3.4 The nginx config is a *main* config, not a `conf.d` fragment

The plan's `nginx.conf` declares `events{}` and `http{}` at the top level, but
copied it to `/etc/nginx/conf.d/default.conf`. That file is included from
*inside* `http{}`, so an `events{}` block there is a parse error. It is now
copied to `/etc/nginx/nginx.conf`.

### 3.5 Plan check #15 (`docker kill` → restart) rests on a wrong assumption

With `restart: unless-stopped`, `docker kill` counts as a **manual** stop and
Docker deliberately does **not** restart the container. The plan expected it to
come back. Real crash recovery (the case that matters) was verified instead:
killing the uvicorn process from inside the container caused the container to
exit and Docker restarted it (`restarts=1`), with health recovering afterwards.
---

## 4. Verification results (2026-10-27)

All Phase 6 gates run against the live stack:

| # | Check | Result |
|---|---|---|
| 1 | Build context excludes venv/node_modules/data | Pass — agent image built from a clean context |
| 2 | Both images build | Pass |
| 3 | Stack up | Pass — both `running` + `healthy` |
| 4 | `GET /healthz` | Pass — `{"status":"ok"}` |
| 5 | Auth'd API through nginx | Pass — `{"backend":"0.1.0","engine":"adk","protocol":1}` |
| 6 | `CONFIG_PATH` resolves to the mounted file | Pass — `/srv/claudio/claudio-backend/config/config.yaml True` |
| 7 | Config volume writable | Pass |
| 8 | LLM reachable from container | Pass — `http://192.168.1.172:8731/v1` |
| 9 | Outbound HTTPS (MCP) | Pass — `api.skillmd.com` |
| 10 | SSE streams incrementally | Pass — frames spread over time (`run_started` 04.777 → `run_completed` 05.898), not one buffered flush |
| 11 | Per-service stop | Pass — `stop web` left the agent running; `status` exited 1 |
| 12 | Per-service start | Pass |
| 13 | Data survives restart | Pass — volumes, not container filesystem |
| 14 | Workspace visible both directions | Pass — host→container and container→host |
| 15 | Crash recovery | Pass — killed uvicorn, container restarted, health recovered |
| 16 | Restart policy | Pass — `unless-stopped` |

`./docker/smoke_test.sh` → **12 passed, 0 failed, 0 skipped**.

---

## 5. Operational notes

**First boot.** The config volume is seeded from `config.example.yaml`, which
contains `CHANGE_ME` placeholders. This is intentional: the real values arrive
via `CLAUDIO__*` environment variables, which take precedence over the YAML.
If you drop the env vars you must edit the file in the volume.

**Config edits from the UI** write to the `claudio-config` volume and take
effect on the next `agent restart` (or immediately for hot-reloadable keys).
`server.host` and `server.port` need a full process restart.

**Rebuild after code changes.**

```bash
./claudio-ctl build          # or: docker compose build
./claudio-ctl restart
./docker/smoke_test.sh
```

**Immutable tags / rollback.** Build and pin per the plan's tag discipline:

```bash
CLAUDIO_TAG=$(git rev-parse --short HEAD) docker compose build
CLAUDIO_TAG=$(git rev-parse --short HEAD) docker compose up -d
```

**Bad config in the volume (L3 rollback).**

```bash
docker compose down
docker volume rm claudio_claudio-config
./claudio-ctl start          # entrypoint reseeds from config.example.yaml
```

**Host-native path still works.** `backend-start.sh` / `backend-stop.sh` are
untouched. Do not run both the host backend and the agent container expecting
them to share state — they use separate `data/` trees.

---

## 6. Security posture

- Agent runs **unprivileged** (uid/gid 1000, matching the host user so
  bind-mounted repos stay writable).
- Agent publishes **no host port**. It is reachable only from `claudio-net`.
  The commented `127.0.0.1:8100` mapping in `compose.yaml` is for debugging
  only — leaving it enabled exposes a shell-capable service to the LAN.
- No secrets are baked into any image. Secrets live in `.env` (gitignored) and
  the `claudio-config` volume.
- `.dockerignore` excludes `config.yaml`, `.env`, `*.db`, `*.log` so runtime
  state and secrets cannot leak into layers.
- The agent container is **trusted-but-bounded**: it runs shell commands, so
  the security boundary is which directories it is mounted into, not the
  container itself. Keep `workspace_allowed_roots` set.

---

## 7. Remaining work (unchanged from the plan)

1. **`CLAUDIO_CONFIG_PATH` env override** — removes the `parents[2]`
   assumption entirely. Still worth doing; the current setup works but depends
   on the install layout staying put.
2. **Postgres instead of SQLite** — prerequisite for >1 agent replica.
3. **TLS** — Caddy/Traefik with Let's Encrypt in front of `claudio-web`.
4. **Auth in front of the whole stack** — the bearer token is currently the
   only gate; nginx does not enforce it on `/api`.
5. **CI pipeline** — build + push images, run `docker/smoke_test.sh` per push.
6. **Resource limits** — `mem_limit`/`cpus` on the agent once steady-state
   numbers are known.
7. **Backup job** for the `claudio-data` volume.
