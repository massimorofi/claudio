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

`claudio-ctl start` also starts a host-side command runner on a protected Unix
socket in `.runtime/`. The agent container connects to it to execute shell
commands as the host user, using the session workspace as the working
directory. `/workspace` is mapped back to `CLAUDIO_WORKSPACE_DIR`, and other
working directories must be under the configured host projects root. Set
`CLAUDIO_HOST_EXECUTION_ENABLED=false` in `.env` to keep commands in the
container instead. Use `claudio-ctl` rather than `docker compose up` when host
execution is enabled; Compose alone does not start the host-side runner.

The runner uses the existing Claudio approval mode (`off`, `on_request`, or
`always`). This grants shell commands the full filesystem and process
permissions of the host user; setting the working directory to the workspace
does **not** sandbox commands or prevent them from accessing other host files.
The runner starts with a minimal environment so variables loaded from `.env`
are not inherited by host commands.
The runner has no network listener, and the container receives a read-only
mount of its mode-0600 Unix socket. `CLAUDIO_UID` must match the host user's
UID so the backend can connect.

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
- With host execution enabled, the runner executes commands as the host user.
  The selected workspace is only the working directory, not a sandbox or
  filesystem boundary. Use `CLAUDIO_HOST_EXECUTION_ENABLED=false` to keep shell
  commands inside the container.

---

## 7. GitHub authentication inside the container

The agent frequently needs GitHub (reading the project board, issues, PRs).
This is a recurring failure point because **the container is not the host**.
The full operational guidance lives in the `git-github` skill
(`claudio-backend/skills/git-github/SKILL.md`); the deployment-specific
facts are here.

### 7.1 Why host login does not transfer

Verified on this stack (2026-11-07):

- The agent runs as **uid 1000 `claudio`**, not the host user. `su <hostuser>`
  fails — that user is not in the container's `/etc/passwd`.
- **No root, no sudo** inside the container.
- Only the project dir and the workspace are bind-mounted. The host's
  `~/.config/gh/hosts.yml`, `~/.git-credentials`, `~/.netrc` and the GNOME
  keyring are **outside the mount and invisible** to the agent.
- `/var/run/docker.sock` is not mounted, so the agent cannot `docker exec`
  into host containers either.

A `gh auth login` performed on the host therefore does **nothing** for the
agent. The agent needs its own credential.

### 7.2 Recommended: inject a PAT via `.env`

Create a Personal Access Token on GitHub and pass it through the environment.
For **GitHub Projects V2** (the `.../projects/N` boards) the token **must**
include the **`project`** scope — without it the board returns **404** even
when the issues inside it are public.

Add to the `agent` service in `compose.yaml`:

```yaml
    environment:
      GH_TOKEN: ${GH_TOKEN:?set GH_TOKEN in .env}
```

and to `.env`:

```
GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

Remember the §3.2 rule: single-quote any JSON-valued key. A bare token is
fine unquoted. `gh` and `git` both pick `GH_TOKEN` up automatically.

### 7.3 Alternative: device-flow login inside the container

`gh` is not in the base image and cannot be `apt-get` installed without
root. Install it into `~/.local/bin` (no root needed) and run the OAuth
device flow — the user opens `https://github.com/login/device` on their own
machine and types a short code. Detach the blocking login with
`setsid ... > log 2>&1 < /dev/null & disown`, read the one-time code from
the log, and poll `gh auth status`. The token is written to
`~/.config/gh/hosts.yml` **inside the container** and is lost when the
container is recreated — prefer §7.2 for anything durable.

### 7.4 Scope cheat-sheet

| Task | Required scope |
|---|---|
| Code, PRs, issues | `repo` |
| **Projects V2 board** | **`project`** |
| Org / teams | `read:org` / `admin:org` |
| Releases | `repo` |

---

## 8. Remaining work (unchanged from the plan)

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
