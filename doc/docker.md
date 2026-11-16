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

---

## 9. Gortex code discovery in Docker (issue #54)

Gortex is an optional code-intelligence MCP server. The shipped config carries a
`gortex` entry that is **disabled by default**; this section is what changes when
you turn it on in a containerised deployment.

### 9.1 The rule that decides everything

A `transport: stdio` MCP server is spawned as a **child process of the backend
process** (`StdioServerParameters(command=..., args=...)` in
`claudio_backend/engine/adk_engine.py`). Therefore:

> **The `gortex` binary must be installed *inside the `claudio/agent` container*,
> on a directory that is on the *backend process's* `PATH`.** A Gortex installed
> on the host is invisible to the container — the container has its own
> filesystem and its own `PATH`.

Two consequences that are easy to get wrong:

1. **`$HOME/.local/bin` is not enough.** Gortex's one-line installer
   (`curl -fsSL https://get.gortex.dev | sh`) drops the binary in
   `$HOME/.local/bin` and appends a PATH line to `.bashrc`. The entrypoint
   `exec`s `uvicorn` directly — it is **not** a login/interactive bash shell, so
   `.bashrc` is never sourced and that PATH line has no effect on the process
   that spawns `gortex mcp`. Install to a directory already on the image `PATH`
   (the `python:3.11-slim` default `PATH` includes `/usr/local/bin`), i.e. set
   `GORTEX_INSTALL_DIR=/usr/local/bin`, or use the `.deb` package which installs
   system-wide.
2. **The binary must be executable by the runtime user.** The backend runs as
   `claudio` (uid/gid 1000, see `docker/backend/Dockerfile`). A system-wide
   install (`/usr/local/bin`, world-readable/executable) satisfies this; a
   root-only install does not.

### 9.2 The path Gortex indexes must be the path the agent sees

Gortex returns **file paths** from the index it built. The agent then opens those
paths with its own file tools. For the answers to be usable, Gortex must index
the repository **at the same in-container path the agent uses**. In this stack the
repo is bind-mounted at `/workspace` (and the host projects root is mounted at
its *same* host path — see `compose.yaml` → `volumes`). So:

- If Gortex runs **inside the agent container**, it already sees `/workspace`
  (same mount) — index `/workspace`.
- If Gortex runs in a **separate container**, bind-mount the *same* host directory
  at the *same* container path (`${CLAUDIO_WORKSPACE_DIR}:/workspace`), otherwise
  Gortex returns host paths the agent cannot open (or vice-versa). This is the
  single most common reason "Gortex found it but the agent can't read it".

### 9.3 Option A — Gortex inside the agent container (stdio)

Add to `docker/backend/Dockerfile`, in the **root** stage (before `USER claudio`),
alongside the existing `apt-get` layer:

```dockerfile
# Gortex code-intelligence binary (optional; issue #54). Installed system-wide
# so it is on the backend process PATH (/usr/local/bin) and executable by the
# unprivileged `claudio` user. Pin a version for reproducible builds.
RUN curl -fsSL https://get.gortex.dev \
      | GORTEX_INSTALL_DIR=/usr/local/bin GORTEX_NO_PATH=1 GORTEX_VERSION=v0.15.0 sh \
    && gortex version
```

Then give the index a place to survive restarts. Gortex keeps its daemon state
and index under a data directory (check `gortex --help` / its onboarding docs for
the exact location; commonly an XDG data dir under the user's home). Mount a
named volume there so a container restart does not force a full re-index, e.g.
add to the `agent` service in `compose.yaml`:

```yaml
    volumes:
      # ... existing claudio-config / claudio-data / claudio-skills / /workspace ...
      - gortex-home:/home/claudio/.local/share/gortex   # path per gortex docs
```
```yaml
volumes:
  # ... existing ...
  gortex-home:
```

Enable it in the config (the entry already exists, disabled):

```yaml
mcp:
  servers:
    - id: gortex
      transport: stdio
      command: [gortex, mcp]
      enabled: true
```

Index the repo once (the daemon auto-starts on first `gortex mcp`, or start it
explicitly). Because the agent container's entrypoint `exec`s uvicorn as its only
process, run indexing as a one-off rather than expecting a supervisor to do it:

```bash
docker compose exec agent gortex daemon start --detach
docker compose exec agent gortex init /workspace     # or gortex's register command
```

Then apply the config change and confirm the tools appear:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8100/agent/restart
```

**Trade-off:** the daemon lives and dies with the agent container. If it crashes,
the Gortex tools silently disappear from the catalog (the engine logs a warning
and skips the server — a run never breaks, but the model stops seeing the tools).
For anything beyond a small pilot, prefer Option B.

### 9.4 Option B — Gortex as its own service (streamable_http) — IMPLEMENTED

This is the shipped Option B. The Gortex daemon runs in its own container on the
`claudio-net` bridge and serves the MCP Streamable HTTP transport at
`http://gortex:7411/mcp`; the agent connects over HTTP by service name (Docker DNS
resolves `gortex` on the bridge). Files:

| File | Role |
|---|---|
| `docker/gortex/Dockerfile` | Debian slim + the signed Gortex binary installed to `/usr/local/bin` (on PATH), unprivileged `gortex` user (uid 1000) |
| `docker/gortex/entrypoint.sh` | `gortex track /workspace` then `exec gortex daemon start --http-addr 0.0.0.0:7411` (foreground = PID 1's child, so signals + restart policy work) |
| `compose.yaml` → `gortex` service | profile `gortex`, same `/workspace` bind mount as the agent, `gortex-data` volume for the index, `/mcp` healthcheck, **no published host port** |
| `claudio-ctl` | `CLAUDIO_GORTEX_ENABLED=true` adds `--profile gortex` and manages the service |
| `config.yaml` / `config.example.yaml` | `mcp.servers` gortex entry → `transport: streamable_http`, `url: http://gortex:7411/mcp` |

Why a separate container instead of stdio in the agent image: the daemon
lifecycle, indexing and index persistence are isolated from the agent, and the
agent just reconnects over HTTP. The daemon serves `/mcp` (MCP) plus `/v1/*`
(JSON API) when started with `--http-addr` (see Gortex `docs/cli.md`).

**Enable it:**

```bash
# 1. turn the profile on for claudio-ctl and start the stack
echo 'CLAUDIO_GORTEX_ENABLED=true' >> .env
#    (the live config.yaml already has the gortex entry enabled: true)
./claudio-ctl start

#    ...or without claudio-ctl:
#    docker compose --profile gortex up -d

# 2. restart the agent so it rebuilds its MCP toolset and picks the tools up
./claudio-ctl restart agent
```

The agent's `mcp.servers` gortex entry is **enabled in the live `config.yaml`**
(this deployment runs Option B) but **disabled in the committed
`config.example.yaml`**, so a fresh deploy that does not run the container is
unaffected. If the gortex container is not running while the entry is enabled, the
agent fails fast to resolve `gortex`, logs a warning and skips it — no breakage;
set `enabled: false` to silence that.

**Auth (required, not optional).** Gortex *refuses* a non-localhost `--http-addr`
bind without a bearer token (`--http-addr "0.0.0.0:7411" is non-localhost;
--http-auth-token ... is required`). Because the agent reaches it across
containers, the bind must be `0.0.0.0`, so a token is mandatory. The wiring:

- the container gets `GORTEX_DAEMON_HTTP_TOKEN` (from `GORTEX_HTTP_TOKEN` in
  `.env`) and passes `--http-auth-token`;
- the agent's `gortex` entry sends the same secret via a new
  `MCPServerEntry.headers` field → `Authorization: Bearer <token>`, forwarded to
  `StreamableHTTPConnectionParams(headers=...)`.

The two must match. The default `claudio-gortex-internal-token` is a placeholder —
**change `GORTEX_HTTP_TOKEN` in `.env` and the `Authorization` header in the
config together.** The service still publishes **no host port**, so the endpoint
is reachable only from `claudio-net` even with the token. Verified live: `/mcp`
returns 401 without the token and a non-401 with it.

If Gortex runs on the **host** instead of a container, reach it via
`host.docker.internal` (add `extra_hosts: ["host.docker.internal:host-gateway"]`
to the `agent` service) — a bare `localhost` URL inside the container points at
the container itself.

### 9.5 Verifying it actually works

1. `docker compose --profile gortex ps` shows `claudio-gortex` **healthy** (the
   healthcheck curls `http://127.0.0.1:7411/mcp`; a 4xx still counts as "listening").
   `docker compose --profile gortex exec gortex gortex status` shows the tracked
   repo and index state.
2. From the **agent** container, confirm the endpoint is reachable by service name:
   `docker compose exec agent curl -s -o /dev/null -w '%{http_code}\n' http://gortex:7411/mcp`.
   A number (not a curl error) means DNS + routing on `claudio-net` work.
3. After `./claudio-ctl restart agent`, the agent's connected-MCP catalog (the
   "Connected MCP servers" block in the system prompt) lists `gortex` with its
   tools. If the backend log shows `MCP server gortex failed to load`, the gortex
   container is down or the URL/port is wrong.
4. Ask a structural question and demand a **tool evidence trail** (which Gortex
   query, which paths/symbols returned). The returned paths must be openable by the
   agent's `read_file` — if they are not, the index path ≠ the agent path (§9.2).
5. Confirm the **Codebase lookup policy** is now in the prompt: it is injected
   automatically only while a connected server's id contains `gortex`
   (`render_code_discovery_policy`), and disappears again when you disable it.
6. Check index freshness after edits — Gortex results are navigation, not proof
   the working tree is current; the policy tells the model to re-read before edits.

### 9.6 What is verified vs. version-dependent

- **Verified from this repo (and a live container run):** the stdio-spawn traps
  (§9.1), the `/workspace` same-path rule (§9.2), the gated policy injection, the
  compose service shape (profile, internal-only network, no host port, `/mcp`
  healthcheck), config URL host/port matching the service, and — built and run
  against a real repo — that the daemon runs as PID 1 in the foreground, indexes
  the mount, and that `/mcp` returns **401 without** the bearer token and a
  non-401 **with** it (so the token requirement and the `headers` wiring are
  proven, not assumed).
- **From Gortex's own docs (pin/confirm for your version):** the release install
  command and version tag, `gortex daemon start --http-addr <addr>` serving `/mcp`
  (and `/v1/*`), `gortex track <path>`, and the daemon data dir under `$HOME/.gortex`
  (mounted as the `gortex-data` volume). Gortex publishes signed binaries for
  linux/amd64 and linux/arm64; the image uses the one-line installer with
  `GORTEX_INSTALL_DIR=/usr/local/bin` (Debian base, not the Alpine `.apk`).
