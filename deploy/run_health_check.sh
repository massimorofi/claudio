#!/usr/bin/env bash
# Claudio health check — systemd timer entry point (Phase 1: Watch).
#
# Runs one read-only health check via the Claudio CLI, which:
#   * runs the deterministic diagnostics (disk, memory, load, failed units,
#     journal errors, backup freshness),
#   * records the result to the database,
#   * raises an issue and sends the configured notification on WARNING/CRITICAL.
#
# The verdict logic lives in the Python engine (claudio.autonomous.health), not
# here, so the timer-driven check behaves exactly like the in-process scheduler
# and stays covered by the test suite. This script only wires it to systemd.
#
# Exit code mirrors the verdict:
#   0 = OK   1 = WARNING   2 = CRITICAL   3 = the check itself errored
#
# This script performs NO mutations. It is read-only monitoring.

set -euo pipefail

# Resolve the backend directory from this script's location so the unit works
# regardless of where it is installed. Override with CLAUDIO_BACKEND if needed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${CLAUDIO_BACKEND:-$(cd "$SCRIPT_DIR/.." && pwd)/backend}"

# Prefer the project venv; fall back to system python3 if it is missing.
if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
    PYTHON="$BACKEND_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

export PYTHONPATH="$BACKEND_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -d "$BACKEND_DIR/src/claudio" ]; then
    echo "ERROR: claudio package not found under $BACKEND_DIR/src" >&2
    exit 3
fi

exec "$PYTHON" -m claudio.autonomous.cli health
