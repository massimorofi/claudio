#!/usr/bin/env bash
# claudio-agent container entrypoint.
#
# Responsibilities:
#   1. Make sure the config volume has a usable config.yaml (seed on first boot).
#   2. Make sure the runtime directories exist and are writable.
#   3. Hand over to uvicorn as PID 1's child (exec, so signals reach the app).
#
# Everything here is idempotent: it is safe to run this on every container start,
# including restarts of a long-lived volume.
set -euo pipefail

# The backend derives its base directory from the installed package location
# (Path(__file__).resolve().parents[2] == claudio-backend). Override with
# CLAUDIO_BASE_DIR only if you know why you are doing it.
BASE_DIR="${CLAUDIO_BASE_DIR:-/srv/claudio/claudio-backend}"
CONFIG_DIR="$BASE_DIR/config"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
DATA_DIR="$BASE_DIR/data"
SEED="/opt/claudio/seed/config.example.yaml"

log() { printf '[entrypoint] %s\n' "$*"; }

# --- 1. config ---------------------------------------------------------------
mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
  if [ ! -f "$SEED" ]; then
    log "FATAL: no config.yaml in $CONFIG_DIR and no seed at $SEED"
    exit 1
  fi
  log "no config.yaml in $CONFIG_DIR — seeding from example"
  cp "$SEED" "$CONFIG_FILE"
  log "seeded. NOTE: $CONFIG_FILE still contains CHANGE_ME placeholders;"
  log "either edit it or set the matching CLAUDIO__* environment variables."
fi

# Fail fast on an unreadable/unparsable config rather than starting a half-dead app.
if ! python -c "
import sys, yaml
from pathlib import Path
p = Path('$CONFIG_FILE')
try:
    data = yaml.safe_load(p.read_text()) or {}
except Exception as e:
    print(f'config.yaml is not valid YAML: {e}', file=sys.stderr); sys.exit(1)
if not isinstance(data, dict):
    print('config.yaml must contain a mapping at the top level', file=sys.stderr); sys.exit(1)
" ; then
  log "FATAL: refusing to start with an invalid $CONFIG_FILE"
  exit 1
fi

# --- 2. runtime directories --------------------------------------------------
mkdir -p "$DATA_DIR/logs" "$DATA_DIR/sessions" "$DATA_DIR/workspaces"

if [ ! -w "$CONFIG_DIR" ]; then
  log "FATAL: $CONFIG_DIR is not writable (uid=$(id -u) gid=$(id -g))."
  log "The backend rewrites config.yaml at runtime; a read-only mount breaks it."
  exit 1
fi
if [ ! -w "$DATA_DIR" ]; then
  log "FATAL: $DATA_DIR is not writable (uid=$(id -u) gid=$(id -g))."
  exit 1
fi

# --- 3. hand over ------------------------------------------------------------
HOST="${CLAUDIO__SERVER__HOST:-0.0.0.0}"
PORT="${CLAUDIO__SERVER__PORT:-8100}"

log "starting uvicorn on ${HOST}:${PORT}"
exec uvicorn claudio_backend.app:app \
  --host "$HOST" \
  --port "$PORT" \
  --log-level warning \
  --proxy-headers \
  --forwarded-allow-ips '*'
