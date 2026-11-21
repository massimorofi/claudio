#!/usr/bin/env bash
# install-engine.sh — build and start the Claudio backend container with an
# alternative agent engine (OpenCode or Claude Code), then configure credentials.
#
# Issue #57. This automates the manual steps in doc/engine-setup.md:
#   1. ensure a .env exists (seeded from .env.example),
#   2. prompt for the credentials the chosen engine needs,
#   3. build the engine-specific agent image,
#   4. start the standard web + agent stack,
#   5. verify /version reports the selected engine.
#
# It is idempotent: re-running re-prompts and updates .env in place.
#
# Usage:
#   scripts/install-engine.sh opencode
#   scripts/install-engine.sh claude_code
#   scripts/install-engine.sh opencode --no-build      # reuse existing image
#   scripts/install-engine.sh claude_code --no-start   # build only
#
# Requires: docker with the compose plugin, curl. Run from the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENGINE="${1:-}"
shift || true
DO_BUILD=1
DO_START=1
for arg in "$@"; do
  case "$arg" in
    --no-build) DO_BUILD=0 ;;
    --no-start) DO_START=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

case "$ENGINE" in
  opencode|claude_code) : ;;
  *) echo "usage: $0 {opencode|claude_code} [--no-build] [--no-start]" >&2; exit 2 ;;
esac

ENV_FILE=".env"
# Engine ids use an underscore (claude_code); override files use a hyphen.
case "$ENGINE" in
  claude_code) _slug="claude-code" ;;
  *) _slug="$ENGINE" ;;
esac
OVERRIDE="compose.engine-${_slug}.yaml"

# --- helpers -----------------------------------------------------------------
log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# Set or append KEY=VALUE in .env without duplicating the key.
set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    # Replace the existing line (value may contain slashes -> use | delimiter).
    python3 - "$ENV_FILE" "$key" "$value" <<'PY'
import sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().splitlines()
out = []
done = False
for ln in lines:
    if ln.startswith(key + "="):
        out.append(f"{key}={value}")
        done = True
    else:
        out.append(ln)
if not done:
    out.append(f"{key}={value}")
open(path, "w").write("\n".join(out) + "\n")
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# Prompt with an optional default (blank default keeps the current value).
prompt() {
  local prompt="$1" default="${2:-}" secret="${3:-}" reply
  if [ -n "$default" ]; then
    printf '%s [%s]: ' "$prompt" "$default"
  else
    printf '%s: ' "$prompt"
  fi
  if [ "$secret" = "secret" ]; then
    IFS= read -rs reply; echo
  else
    IFS= read -r reply
  fi
  if [ -z "$reply" ]; then reply="$default"; fi
  printf '%s' "$reply"
}

# --- preconditions -----------------------------------------------------------
command -v docker >/dev/null || die "docker is required"
docker compose version >/dev/null 2>&1 || die "docker compose plugin is required"
[ -f "$OVERRIDE" ] || die "missing $OVERRIDE (are you at the repo root?)"

# --- 1. ensure .env ----------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  log "created .env from .env.example"
fi

# --- 2. credentials ----------------------------------------------------------
log "Configuring shared backend settings (press Enter to keep the current value)."
AUTH_TOKEN="$(grep -E '^CLAUDIO__SERVER__AUTH_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
set_env "CLAUDIO__SERVER__AUTH_TOKEN" "$(prompt 'Backend auth token' "${AUTH_TOKEN:-dev-token-change-me}" secret)"

LLM_BASE="$(grep -E '^CLAUDIO__LLM__BASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
LLM_KEY="$(grep -E '^CLAUDIO__LLM__API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
LLM_MODEL="$(grep -E '^CLAUDIO__LLM__MODEL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"

if [ "$ENGINE" = "opencode" ]; then
  log "OpenCode uses an OpenAI-compatible endpoint (the same one as the rest of Claudio)."
  set_env "CLAUDIO__LLM__BASE_URL" "$(prompt 'OpenAI-compatible base URL' "${LLM_BASE:-http://127.0.0.1:1234/v1}")"
  set_env "CLAUDIO__LLM__API_KEY"  "$(prompt 'LLM API key' "${LLM_KEY:-local}" secret)"
  set_env "CLAUDIO__LLM__MODEL"    "$(prompt 'Model name' "${LLM_MODEL:-qwen3.5-4b}")"
  set_env "CLAUDIO_ENGINE" "opencode"
  # Pin the OpenCode CLI version baked into the image (do not use 'latest').
  set_env "OPENCODE_VERSION" "$(prompt 'OpenCode CLI version to pin' '0.0.0-pin-me')"
elif [ "$ENGINE" = "claude_code" ]; then
  log "Claude Code speaks the Anthropic Messages API (/v1/messages)."
  log "Give the Anthropic-compatible base URL + key. An OpenAI-only endpoint is"
  log "rejected at startup unless you explicitly allow it after verifying a gateway."
  set_env "CLAUDIO__LLM__BASE_URL" "$(prompt 'Anthropic Messages base URL' "${LLM_BASE:-https://api.anthropic.com/v1}")"
  AK="$(prompt 'Anthropic API key' "${LLM_KEY:-}" secret)"
  set_env "CLAUDIO__LLM__API_KEY" "$AK"
  # Mirror the key into ANTHROPIC_API_KEY (used by the compose override).
  set_env "ANTHROPIC_API_KEY" "$AK"
  set_env "CLAUDIO__LLM__MODEL" "$(prompt 'Claude Code model id' "${LLM_MODEL:-claude-sonnet-4-5}")"
  set_env "CLAUDIO_ENGINE" "claude_code"
  set_env "CLAUDE_CODE_VERSION" "$(prompt 'Claude Code CLI version to pin' '0.0.0-pin-me')"
  ALLOW="$(prompt 'Endpoint is Anthropic-compatible / verified gateway? (true/false)' 'false')"
  set_env "CLAUDE_CODE_ALLOW_OPENAI_ENDPOINT" "$ALLOW"
fi

log "wrote settings to .env (secrets are stored there; it is gitignored)."

# --- 3. build -----------------------------------------------------------------
COMPOSE_FILES=(-f compose.yaml -f "$OVERRIDE")
if [ "$DO_BUILD" = "1" ]; then
  log "building the $ENGINE agent image (this installs the pinned CLI)..."
  docker compose "${COMPOSE_FILES[@]}" build agent
else
  log "skipping build (--no-build)."
fi

# --- 4. start -----------------------------------------------------------------
if [ "$DO_START" = "1" ]; then
  log "starting web + agent..."
  docker compose "${COMPOSE_FILES[@]}" up -d
  log "waiting for the agent to become healthy..."
  for _ in $(seq 1 30); do
    if docker compose "${COMPOSE_FILES[@]}" ps agent 2>/dev/null | grep -q "healthy"; then
      break
    fi
    sleep 2
  done

  # --- 5. verify -------------------------------------------------------------
  TOKEN="$(grep -E '^CLAUDIO__SERVER__AUTH_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  PORT="$(grep -E '^CLAUDIO_HTTP_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  PORT="${PORT:-8080}"
  log "checking /version through the web proxy on port $PORT..."
  if command -v curl >/dev/null; then
    curl -fsS -H "Authorization: Bearer ${TOKEN}" "http://localhost:${PORT}/api/v1/version" \
      && echo || warn "could not reach /version (is the web service up on $PORT?)"
  fi
  log "done. Engine '$ENGINE' selected. Open http://localhost:${PORT} and sign in with the auth token."
else
  log "built but not started (--no-start). Start with:"
  echo "  docker compose ${COMPOSE_FILES[*]} up -d"
fi
