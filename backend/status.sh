#!/usr/bin/env bash
# Report supervisor + agent runtime status.
# Exit codes: 0 healthy, 1 down, 2 degraded.
set -uo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$BACKEND_DIR/data/claudio.pid"

read_host_port() {
    python3 - "$BACKEND_DIR" <<'PY' 2>/dev/null || echo "127.0.0.1 8100"
import sys, yaml, os
cfg_path = os.path.join(sys.argv[1], "config", "config.yaml")
if not os.path.exists(cfg_path):
    cfg_path = os.path.join(sys.argv[1], "config", "config.example.yaml")
with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}
s = cfg.get("server", {})
print(s.get("host", "127.0.0.1"), s.get("port", 8100))
PY
}

read() { read_host_port; }
set -- $(read)
HOST="${1:-127.0.0.1}"
PORT="${2:-8100}"

if [ ! -f "$PID_FILE" ] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "supervisor: DOWN"
    exit 1
fi
echo "supervisor: UP (pid $(cat "$PID_FILE"))"

if ! curl -s -m 2 "http://${HOST}:${PORT}/healthz" >/dev/null 2>&1; then
    echo "http:       UNREACHABLE"
    exit 1
fi
echo "http:       OK"

TOKEN=$(python3 - "$BACKEND_DIR" <<'PY' 2>/dev/null || echo ""
import sys, yaml, os
cfg_path = os.path.join(sys.argv[1], "config", "config.yaml")
if not os.path.exists(cfg_path):
    cfg_path = os.path.join(sys.argv[1], "config", "config.example.yaml")
with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}
print(cfg.get("server", {}).get("auth_token", ""))
PY
)

STATUS=$(curl -s -m 3 -H "Authorization: Bearer ${TOKEN}" \
    "http://${HOST}:${PORT}/api/v1/agent/status" 2>/dev/null || echo "")
if [ -z "$STATUS" ]; then
    echo "agent:      UNKNOWN"
    exit 2
fi
AGENT_STATE=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','unknown'))" 2>/dev/null || echo unknown)
echo "agent:      ${AGENT_STATE}"

case "$AGENT_STATE" in
    running)  exit 0 ;;
    degraded) exit 2 ;;
    *)        exit 1 ;;
esac
