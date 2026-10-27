#!/usr/bin/env bash
# docker/smoke_test.sh — post-build verification gate for the Claudio docker stack.
#
# Runs the Phase 6 checks in order. Each check is pass/fail; the script keeps going
# so you see the whole picture, and exits non-zero if anything failed.
#
#   ./docker/smoke_test.sh            run against localhost:8080
#   BASE_URL=http://host:port ./docker/smoke_test.sh
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 2

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

BASE_URL="${BASE_URL:-http://127.0.0.1:${CLAUDIO_HTTP_PORT:-8080}}"
TOKEN="${CLAUDIO__SERVER__AUTH_TOKEN:?set CLAUDIO__SERVER__AUTH_TOKEN in .env}"
LLM_BASE="${CLAUDIO__LLM__BASE_URL:-}"

PASS=0
FAIL=0
SKIP=0

if [ -t 1 ]; then
  c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_off=$'\033[0m'
else
  c_ok=""; c_bad=""; c_warn=""; c_off=""
fi

pass() { printf '  %sPASS%s  %s\n' "$c_ok" "$c_off" "$1"; PASS=$((PASS+1)); }
fail() { printf '  %sFAIL%s  %s\n' "$c_bad" "$c_off" "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; FAIL=$((FAIL+1)); }
skip() { printf '  %sSKIP%s  %s\n' "$c_warn" "$c_off" "$1"; SKIP=$((SKIP+1)); }

# A non-empty HTTP body is not proof of health: an auth or runtime failure returns
# a JSON error envelope, which is non-empty. Assert the body is not one.
is_error_envelope() { printf '%s' "$1" | grep -q '"error"'; }

printf 'Claudio docker smoke test — %s\n\n' "$BASE_URL"

# 1. containers exist and are running -----------------------------------------
if docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null | grep -q 'claudio-agent running'; then
  pass "agent container running"
else
  fail "agent container running" "docker compose ps shows it absent or not running"
fi

if docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null | grep -q 'claudio-web running'; then
  pass "web container running"
else
  fail "web container running" "docker compose ps shows it absent or not running"
fi

# 2. agent liveness ----------------------------------------------------------
if curl -fsS -m 5 "$BASE_URL/healthz" >/dev/null 2>&1; then
  pass "GET /healthz"
else
  fail "GET /healthz" "no response from $BASE_URL/healthz"
fi

# 3. authenticated API through the proxy -------------------------------------
VER=$(curl -fsS -m 5 -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/version" 2>/dev/null)
if [ -n "$VER" ] && ! is_error_envelope "$VER"; then
  pass "GET /api/v1/version through nginx -> $VER"
else
  fail "GET /api/v1/version through nginx" "bad response: ${VER:-<empty>}"
fi

# 3b. authenticated endpoint that needs the agent runtime ---------------------
ST=$(curl -fsS -m 5 -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/agent/status" 2>/dev/null)
if [ -n "$ST" ] && ! is_error_envelope "$ST"; then
  pass "GET /api/v1/agent/status -> $ST"
else
  fail "GET /api/v1/agent/status" "bad response: ${ST:-<empty>} (token mismatch or agent runtime not started)"
fi

# 4. config volume is writable -----------------------------------------------
if docker compose exec -T agent sh -c 'touch /srv/claudio/claudio-backend/config/.wtest && rm -f /srv/claudio/claudio-backend/config/.wtest' >/dev/null 2>&1; then
  pass "config volume writable"
else
  fail "config volume writable" "uid/gid mismatch on the claudio-config volume"
fi

# 5. config path resolves to the mounted file --------------------------------
CP=$(docker compose exec -T agent python -c \
  'from claudio_backend.app import CONFIG_PATH; print(f"{CONFIG_PATH} {CONFIG_PATH.exists()}")' 2>/dev/null)
if echo "$CP" | grep -q 'True$'; then
  pass "CONFIG_PATH resolves to a real file -> $CP"
else
  fail "CONFIG_PATH resolves to a real file" "got: ${CP:-<error>} — expected .../claudio-backend/config/config.yaml True"
fi

# 6. LLM reachable from inside the agent container ---------------------------
if [ -n "$LLM_BASE" ]; then
  if docker compose exec -T agent curl -fsS -m 10 "${LLM_BASE%/}/models" >/dev/null 2>&1; then
    pass "LLM reachable from container ($LLM_BASE)"
  else
    fail "LLM reachable from container ($LLM_BASE)" "check LAN routing / DNS from the docker bridge"
  fi
else
  skip "LLM reachable (CLAUDIO__LLM__BASE_URL unset)"
fi

# 7. outbound internet (MCP) --------------------------------------------------
if docker compose exec -T agent curl -fsSI -m 10 https://api.skillmd.com/mcp >/dev/null 2>&1 \
   || docker compose exec -T agent curl -sSI -m 10 https://api.skillmd.com/mcp 2>/dev/null | grep -q "HTTP/"; then
  pass "outbound https reachable (api.skillmd.com)"
else
  fail "outbound https reachable (api.skillmd.com)" "container has no internet route"
fi

# 8. SSE streams incrementally through nginx ----------------------------------
# A buffered proxy delivers everything at the end; a correct one delivers early.
# Measure time-to-first-byte separately from total, and require real SSE frames.
SSE_BODY=/tmp/sse_probe.out
rm -f "$SSE_BODY"
T0=$(date +%s.%N)
FIRST=$(curl -N -sS -m 30 -o "$SSE_BODY" -w '%{time_starttransfer}' \
    -X POST "$BASE_URL/api/v1/chat/stream" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"message":"say hi"}' 2>/dev/null)
TTFB=$(python3 -c "print(f'{float(\"${FIRST:-0}\"):.2f}')" 2>/dev/null || echo "?")

if [ ! -s "$SSE_BODY" ]; then
  fail "SSE stream produced data" "empty stream — check proxy_buffering / route name"
elif is_error_envelope "$(head -c 400 "$SSE_BODY")"; then
  fail "SSE stream produced data" "$(head -c 200 "$SSE_BODY")"
elif ! grep -q '^event:\|^data:' "$SSE_BODY"; then
  fail "SSE stream produced data" "no SSE frames (event:/data:) in response"
else
  pass "SSE stream produced real frames (first byte ${TTFB}s, $(grep -c '^data:' "$SSE_BODY" 2>/dev/null || echo '?') data frames)"
fi

# 9. SPA served ---------------------------------------------------------------
if curl -fsS -m 5 "$BASE_URL/" 2>/dev/null | grep -qi '<div id="root"\|<!doctype html>'; then
  pass "SPA index served"
else
  fail "SPA index served" "no HTML at $BASE_URL/"
fi

# 10. restart policy ----------------------------------------------------------
RP=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' claudio-agent 2>/dev/null || echo '?')
if [ "$RP" = "unless-stopped" ]; then
  pass "agent restart policy = unless-stopped"
else
  fail "agent restart policy" "got '$RP', expected 'unless-stopped'"
fi

printf '\n%s%d passed%s, %s%d failed%s, %s%d skipped%s\n' \
  "$c_ok" "$PASS" "$c_off" \
  "$c_bad" "$FAIL" "$c_off" \
  "$c_warn" "$SKIP" "$c_off"

[ "$FAIL" -eq 0 ] || exit 1
