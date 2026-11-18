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
#   GORTEX_EXTRA_REPOS comma-separated additional repository paths to track.
#                      Each must be mounted at its identical host path.
#   GORTEX_MANIFEST    path to write the index manifest the agent reads at
#                      session start (default /workspace/.gortex-index.json).
#   GORTEX_HTTP_PORT   port for --http-addr (default 7411).
set -euo pipefail

REPO="${GORTEX_REPO:-/workspace}"
PORT="${GORTEX_HTTP_PORT:-7411}"
MANIFEST="${GORTEX_MANIFEST:-/workspace/.gortex-index.json}"

log() { printf '[gortex-entrypoint] %s\n' "$*"; }

# A named volume may carry a stale pid/socket from a previous container. Clear it
# so `daemon start` does not refuse to start against a dead daemon.
gortex daemon stop >/dev/null 2>&1 || true

# Register every repository with the tracked workspace, then let the daemon index
# them on start. Tracking is what makes a repo reachable: the daemon refuses a
# path that is not inside its tracked-root set, so a repo that is not bind-mounted
# here fails with "invalid path" rather than being silently skipped.
#
# Tracking happens BEFORE the daemon starts (config-only) and the results are
# recorded in a manifest the agent reads at session start. That manifest is the
# whole point: it turns "is the code graph usable for this workspace?" from a
# question the model has to guess at into a fact it is told up front.
TRACKED_REPOS=()
TRACK_FAILED=()
# Split GORTEX_EXTRA_REPOS on commas (NOT whitespace): repository paths may
# legitimately contain spaces, and a naive `for r in $VAR` splits on IFS's
# default whitespace, which leaves a trailing comma glued to each path.
EXTRA_REPOS=()
if [ -n "${GORTEX_EXTRA_REPOS:-}" ]; then
  IFS=',' read -ra EXTRA_REPOS <<< "$GORTEX_EXTRA_REPOS"
fi
for r in "$REPO" "${EXTRA_REPOS[@]:-}"; do
  r="${r//[[:space:]]/}"
  [ -n "$r" ] || continue
  if [ ! -d "$r" ]; then
    log "WARNING: $r not present; nothing to index. Mount it at the same host path."
    TRACK_FAILED+=("$r:absent")
    continue
  fi
  log "tracking $r"
  if gortex track "$r"; then
    TRACKED_REPOS+=("$r")
  else
    log "track failed for $r (continuing)"
    TRACK_FAILED+=("$r:track-failed")
  fi
done

# Emit the manifest: which repos are actually indexed, and which are not. Written
# atomically so a reader never sees a half-written file. `|| true` throughout:
# an unwritable manifest degrades the agent's prompt, it must not stop the daemon.
write_manifest() {
  local tmp tracked="" failed=""
  for r in "${TRACKED_REPOS[@]:-}"; do [ -n "$r" ] && tracked="$tracked\"$r\","; done
  for r in "${TRACK_FAILED[@]:-}"; do [ -n "$r" ] && failed="$failed\"$r\","; done
  tmp="$(dirname "$MANIFEST")/.gortex-index.$$.tmp"
  mkdir -p "$(dirname "$MANIFEST")" 2>/dev/null || return 0
  {
    printf '{\n  "generated_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "tracked_repos": [%s],\n' "${tracked%,}"
    printf '  "failed_repos": [%s]\n' "${failed%,}"
    printf '}\n'
  } > "$tmp" 2>/dev/null && mv -f "$tmp" "$MANIFEST" 2>/dev/null || rm -f "$tmp" 2>/dev/null || true
}
write_manifest || true
log "manifest: ${#TRACKED_REPOS[@]} tracked, ${#TRACK_FAILED[@]} failed -> $MANIFEST"

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
