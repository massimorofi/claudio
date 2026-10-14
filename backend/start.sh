#!/usr/bin/env bash
# Start the Claudio backend (daemonized).
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

VENV="$BACKEND_DIR/.venv"
DATA_DIR="$BACKEND_DIR/data"
PID_FILE="$DATA_DIR/claudio.pid"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/backend.log"

# Read host/port from config (fall back to defaults).
HOST=$(python3 - "$BACKEND_DIR" <<'PY' 2>/dev/null || echo "127.0.0.1"
import sys, yaml, os
cfg_path = os.path.join(sys.argv[1], "config", "config.yaml")
if not os.path.exists(cfg_path):
    cfg_path = os.path.join(sys.argv[1], "config", "config.example.yaml")
with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}
print(cfg.get("server", {}).get("host", "127.0.0.1"))
PY
)
PORT=$(python3 - "$BACKEND_DIR" <<'PY' 2>/dev/null || echo "8100"
import sys, yaml, os
cfg_path = os.path.join(sys.argv[1], "config", "config.yaml")
if not os.path.exists(cfg_path):
    cfg_path = os.path.join(sys.argv[1], "config", "config.example.yaml")
with open(cfg_path) as f:
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
    "$VENV/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt"
fi

# Check port free.
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
    echo "Port ${PORT} is already in use"
    exit 1
fi

echo "Starting Claudio backend on ${HOST}:${PORT} ..."
PYTHONPATH="$BACKEND_DIR/src" nohup "$VENV/bin/uvicorn" claudio.main:app \
    --host "$HOST" --port "$PORT" --log-level warning \
    >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

# Poll /healthz up to 15s.
for i in $(seq 1 30); do
    if curl -s -m 2 "http://${HOST}:${PORT}/healthz" >/dev/null 2>&1; then
        echo "Started (pid $(cat "$PID_FILE"))"
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
