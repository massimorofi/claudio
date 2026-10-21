#!/usr/bin/env bash
# Start the Claudio backend (daemonized).
#
# Launches uvicorn against claudio_backend.app:app on the host/port from
# claudio-backend/config/config.yaml (defaults 127.0.0.1:8100), waits for
# /healthz, and records the PID in claudio-backend/data/claudio.pid.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_DIR/claudio-backend"

VENV="$BACKEND_DIR/.venv"
DATA_DIR="$BACKEND_DIR/data"
PID_FILE="$DATA_DIR/claudio.pid"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/backend.log"

# Read host/port from config (fall back to defaults).
CONFIG_YAML="$BACKEND_DIR/config/config.yaml"
HOST=$("$VENV/bin/python" - "$CONFIG_YAML" <<'PY' 2>/dev/null || echo "127.0.0.1"
import sys, yaml, os
path = sys.argv[1]
if not os.path.exists(path):
    print("127.0.0.1"); sys.exit(0)
with open(path) as f:
    cfg = yaml.safe_load(f) or {}
print(cfg.get("server", {}).get("host", "127.0.0.1"))
PY
)
PORT=$("$VENV/bin/python" - "$CONFIG_YAML" <<'PY' 2>/dev/null || echo "8100"
import sys, yaml, os
path = sys.argv[1]
if not os.path.exists(path):
    print("8100"); sys.exit(0)
with open(path) as f:
    cfg = yaml.safe_load(f) or {}
print(cfg.get("server", {}).get("port", 8100))
PY
)
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8100}"

mkdir -p "$DATA_DIR" "$LOG_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Already running (pid $(cat "$PID_FILE"))"
    exit 1
fi

# Create venv if missing.
if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating virtualenv..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e "$REPO_DIR/claudio-core"
    "$VENV/bin/pip" install --quiet -e "$BACKEND_DIR[dev]"
fi

# Check port free.
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
    echo "Port ${PORT} is already in use"
    exit 1
fi

echo "Starting Claudio backend on ${HOST}:${PORT} ..."
nohup "$VENV/bin/uvicorn" claudio_backend.app:app \
    --host "$HOST" --port "$PORT" --log-level warning \
    >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

# Poll /healthz up to 15s.
for i in $(seq 1 30); do
    if curl -s -m 2 "http://${HOST}:${PORT}/healthz" >/dev/null 2>&1; then
        echo "Started (pid $(cat "$PID_FILE")) — http://${HOST}:${PORT}"
        exit 0
    fi
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Process died during startup. Last log lines:"
        tail -n 20 "$LOG_FILE" || true
        rm -f "$PID_FILE"
        exit 1
    fi
    sleep 0.5
done

echo "Startup timed out after 15s. Last log lines:"
tail -n 20 "$LOG_FILE" || true
exit 1
