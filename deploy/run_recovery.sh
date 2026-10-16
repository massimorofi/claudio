#!/usr/bin/env bash
# Claudio disaster recovery — systemd boot entry point.
#
# Runs one disaster-recovery pass via the Claudio CLI, which:
#   * verifies backup freshness first (missing/stale backup -> CRITICAL),
#   * restarts failed units that are on the restart allowlist (once per boot),
#   * cleans up disk if it is full,
#   * restarts expected services that are down,
#   * alerts immediately on kernel filesystem errors (never fsck a live root),
#   * raises issues and notifies on WARNING/CRITICAL.
#
# The recovery logic lives in the Python engine (claudio.autonomous.recovery),
# not here, so the boot-driven pass behaves exactly like the in-process path
# and stays covered by the test suite. This script only wires it to systemd.
#
# Exit code mirrors the verdict:
#   0 = OK   1 = WARNING   2 = CRITICAL   3 = the pass itself errored
#
# This unit is enabled at boot. Boot-time recovery is inherently a systemd
# concern and should run even if the backend service is down.

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

exec "$PYTHON" -m claudio.autonomous.cli recovery
