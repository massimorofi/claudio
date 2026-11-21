# Running the backend with OpenCode or Claude Code (issue #57)

This guide shows how to build and run the Claudio backend container driven by an
alternative agent engine — **OpenCode** or **Claude Code** — instead of the
default in-process ADK engine. The public API, web UI, approvals, sessions and
tool policy are unchanged; only the engine behind the `agent` container differs.

There are two paths:

- **Automated installer** (`scripts/install-engine.sh`) — builds the container,
  prompts for credentials, starts the stack, and verifies the engine. Recommended.
- **Manual steps** — the same thing by hand, for understanding or CI.

> One engine per backend process (deployment-wide, not per session). The default
> `adk` stack needs none of this; you only follow this guide to switch engines.

---

## 0. Prerequisites

- Docker Engine with the **Compose v2** plugin (`docker compose version`).
- `curl` and `python3` on the host (the installer uses them).
- You are in the repo root (where `compose.yaml` lives).
- For **Claude Code**: an **Anthropic Messages-compatible** endpoint
  (`.../v1/messages`). Claude Code is *not* automatically compatible with an
  OpenAI-only `/v1/chat/completions` server. If your model server is OpenAI-only,
  you must front it with a conversion gateway that exposes `/v1/messages`, or use
  the `adk` engine instead.
- For **OpenCode**: any OpenAI-compatible endpoint (the same one Claudio already
  uses).

---

## 1. Automated installer (recommended)

The installer creates `.env` if missing, prompts for the credentials the chosen
engine needs, builds the engine-specific image, starts `web` + `agent`, and
checks `/version`.

### OpenCode

```bash
scripts/install-engine.sh opencode
```

You will be prompted for:

| Prompt | What it is | Example |
|---|---|---|
| Backend auth token | bearer token for the API/UI | a long random string |
| OpenAI-compatible base URL | your model server | `http://192.168.1.172:8731/v1` |
| LLM API key | key for that server | `local` or your key |
| Model name | model id | `qwen3.5-4b` |
| OpenCode CLI version to pin | version baked into the image | e.g. `0.6.5` |

### Claude Code

```bash
scripts/install-engine.sh claude_code
```

You will be prompted for:

| Prompt | What it is | Example |
|---|---|---|
| Backend auth token | bearer token for the API/UI | a long random string |
| Anthropic Messages base URL | endpoint serving `/v1/messages` | `https://api.anthropic.com/v1` |
| Anthropic API key | `ANTHROPIC_API_KEY` | `sk-ant-...` |
| Claude Code model id | model for the CLI | `claude-sonnet-4-5` |
| Claude Code CLI version to pin | version baked into the image | e.g. `1.0.x` |
| Endpoint is Anthropic-compatible / verified gateway? | `true` only if verified | `false` |

The installer writes everything to `.env` (gitignored) and prints the result of
`GET /api/v1/version`, which should show `"engine": "opencode"` or
`"engine": "claude_code"`.

Useful flags:

```bash
scripts/install-engine.sh opencode --no-build     # reuse the existing image
scripts/install-engine.sh claude_code --no-start  # build only, don't start
```

### Re-configuring credentials later

Re-run the installer; it updates `.env` in place (press Enter to keep a current
value). Or edit `.env` directly and restart:

```bash
docker compose -f compose.yaml -f compose.engine-opencode.yaml up -d agent
```

---

## 2. Manual steps

### 2.1 Configure `.env`

```bash
cp .env.example .env   # if you don't already have one
```

Set the shared backend values (always required):

```bash
CLAUDIO__SERVER__AUTH_TOKEN=<a-long-random-token>
CLAUDIO_HTTP_PORT=8080
```

Then the engine-specific block.

**OpenCode** (OpenAI-compatible endpoint):

```bash
CLAUDIO_ENGINE=opencode
CLAUDIO__LLM__BASE_URL=http://<host>:<port>/v1
CLAUDIO__LLM__API_KEY=<key-or-local>
CLAUDIO__LLM__MODEL=<model-id>
OPENCODE_VERSION=<pinned-version>
```

**Claude Code** (Anthropic Messages endpoint):

```bash
CLAUDIO_ENGINE=claude_code
CLAUDIO__LLM__BASE_URL=https://api.anthropic.com/v1   # must serve /v1/messages
CLAUDIO__LLM__API_KEY=<sk-ant-...>
ANTHROPIC_API_KEY=<sk-ant-...>
CLAUDIO__LLM__MODEL=<claude-model-id>
CLAUDE_CODE_VERSION=<pinned-version>
CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT=false   # true ONLY after verifying /v1/messages
```

### 2.2 Build the engine image

The `ENGINE` build arg installs only the selected pinned CLI:

```bash
# OpenCode
docker compose -f compose.yaml -f compose.engine-opencode.yaml build agent

# Claude Code
docker compose -f compose.yaml -f compose.engine-claude-code.yaml build agent
```

### 2.3 Start the stack

```bash
# OpenCode
docker compose -f compose.yaml -f compose.engine-opencode.yaml up -d

# Claude Code
docker compose -f compose.yaml -f compose.engine-claude-code.yaml up -d
```

The `web` service waits for the `agent` to be healthy, then serves the UI on
`http://localhost:${CLAUDIO_HTTP_PORT}` (default 8080).

### 2.4 Verify the engine

```bash
curl -fsS -H "Authorization: Bearer <CLAUDIO__SERVER__AUTH_TOKEN>" \
  http://localhost:8080/api/v1/version
```

Expected (OpenCode example):

```json
{
  "backend": "0.1.0",
  "engine": "opencode",
  "engine_version": "<cli-version>",
  "engine_capabilities": { "streaming": true, "approvals": true, "tools": true, ... },
  "protocol": 1
}
```

---

## 3. Switching engines and rollback

Switching recreates only the `agent` container (no frontend rebuild):

```bash
docker compose -f compose.yaml -f compose.engine-opencode.yaml up -d --build agent
```

Roll back to the default ADK engine:

```bash
docker compose -f compose.yaml up -d --build agent
```

(With no `CLAUDIO_ENGINE` set, the backend defaults to `adk`.)

---

## 4. How credentials reach the engine

- The backend reads `CLAUDIO__LLM__*` (and `CLAUDIO_ENGINE`) from `.env` via
  Compose.
- The **OpenCode** adapter launches `opencode run` with a minimal child
  environment containing `OPENAI_BASE_URL` / `OPENAI_API_KEY` derived from the
  resolved model profile. The whole `.env` is never passed to the CLI.
- The **Claude Code** adapter launches `claude -p` with `ANTHROPIC_BASE_URL` /
  `ANTHROPIC_API_KEY` and a dedicated `CLAUDE_CONFIG_DIR` under the persistent
  data volume. It **refuses to start** against an endpoint that is not declared
  Anthropic-compatible unless `CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT=true`.
- Secrets live only in `.env` (gitignored) and runtime env — never in the image,
  `config.yaml`, logs, or `/config` responses.

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Startup fails: `OpenCode binary 'opencode' not found` | You built the `adk` image but set `CLAUDIO_ENGINE=opencode`. Build with `-f compose.engine-opencode.yaml` (sets `ENGINE=opencode`). |
| Startup fails: `Claude Code requires an Anthropic Messages-compatible endpoint` | Your `CLAUDIO__LLM__BASE_URL` is OpenAI-only. Point it at a `/v1/messages` endpoint (or a verified conversion gateway) and set `CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT=true`. |
| `engine mismatch` / wrong engine in `/version` | `CLAUDIO_ENGINE` does not match the image's `ENGINE` build arg. Rebuild the matching variant. |
| Image build fails installing the CLI | `OPENCODE_VERSION` / `CLAUDE_CODE_VERSION` is still the `0.0.0-pin-me` placeholder. Set a real pinned version. |
| `/version` shows `adk` after switching | You started without the override file. Use `-f compose.yaml -f compose.engine-<engine>.yaml`. |
| UI unreachable | Check `CLAUDIO_HTTP_PORT` and that `agent` is healthy: `docker compose -f compose.yaml -f compose.engine-opencode.yaml ps`. |

---

## 6. What is and isn't proven here

The pluggable architecture, adapters, subprocess runner, config, factory,
checkpoints and deterministic tests (driven by fake CLIs) are complete and
covered by the test suite. Running against the **real** pinned OpenCode CLI and a
**verified** Anthropic-compatible Claude Code endpoint — and building the three
image variants in Docker — are the spec's own go/no-go gates (Phases 2–3) and
require installing those binaries and credentials in your environment. Pin real
CLI versions in the build args before production use.
