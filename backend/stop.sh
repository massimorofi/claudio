#!/usr/bin/env bash
# Stop the Claudio backend gracefully (SIGTERM, 20s grace, then SIGKILL).
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$BACKEND_DIR/data/claudio.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Not running (no pid file)"
    exit 0
fi

PID="$(cat "$PID_FILE")"
if ! kill -0 "$PID" 2>/dev/null; then
    echo "Stale pid file (process $PID not alive); removing"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping (pid $PID)..."
kill -TERM "$PID"

# Wait up to 20s for graceful drain.
for i in $(seq 1 40); do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "Stopped"
        exit 0
    fi
    sleep 0.5
done

echo "Grace period elapsed; sending SIGKILL"
kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "Killed"
