#!/usr/bin/env bash
# claudio-gortex container entrypoint.
#
# Brings up the Gortex daemon so it serves the MCP Streamable HTTP transport at
# /mcp (plus the /v1 JSON API) on the internal bridge, and keeps it in the
# FOREGROUND as the container's main process (PID 1's child) so `docker stop`
# delivers SIGTERM to the daemon and the restart policy works correctly.
#
# Env:
#   GORTEX_REPO        repository path to index (default /workspace — the same
#                      bind mount the agent sees, so returned paths match).
#   GORTEX_HTTP_PORT   port for --http-addr (default 7411).
set -euo pipefail

REPO="${GORTEX_REPO:-/workspace}"
PORT="${GORTEX_HTTP_PORT:-7411}"

log() { printf '[gortex-entrypoint] %s\n' "$*"; }

# A named volume may carry a stale pid/socket from a previous container. Clear it
# so `daemon start` does not refuse to start against a dead daemon.
gortex daemon stop >/dev/null 2>&1 || true

# Register the repository with the tracked workspace. With no daemon running yet
# this is config-only; the daemon picks it up on start and indexes it. `|| true`
# so a non-git or empty mount does not abort the container.
if [ -d "$REPO" ]; then
  log "tracking $REPO"
  gortex track "$REPO" || log "track failed (continuing); index may be empty"
else
  log "WARNING: $REPO not present; nothing to index. Mount the repo at \$GORTEX_REPO."
fi

# Gortex refuses a non-localhost bind without a bearer token (security guard).
# The agent connects over the bridge, so we must bind 0.0.0.0 and therefore must
# supply a token. Gortex reads $GORTEX_DAEMON_HTTP_TOKEN by default; we also pass
# it explicitly. The agent's mcp.servers gortex entry must send the SAME token as
# an "Authorization: Bearer <token>" header (MCPServerEntry.headers).
TOKEN="${GORTEX_DAEMON_HTTP_TOKEN:-${GORTEX_HTTP_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  log "FATAL: GORTEX_DAEMON_HTTP_TOKEN is empty; a non-localhost --http-addr bind"
  log "       requires a token. Set GORTEX_HTTP_TOKEN in .env (compose passes it)."
  exit 1
fi

# Serve HTTP on all interfaces so the agent container can reach it by service
# name over the claudio-net bridge. Without --detach the daemon runs in the
# foreground (see gortex docs/cli.md: --detach only backgrounds the same process).
log "starting daemon, MCP at 0.0.0.0:${PORT}/mcp (auth: bearer token required)"
exec gortex daemon start --http-addr "0.0.0.0:${PORT}" --http-auth-token "$TOKEN"
