#!/usr/bin/env bash
# Claudio maintenance — systemd timer entry point.
#
# Runs one maintenance pass via the Claudio CLI, which:
#   * runs the safe cleanup actions (apt update, journal vacuum, temp cleanup)
#     directly when the daemon is NOT in report-only mode,
#   * records the irreversible actions (autoremove, full upgrade, docker prune)
#     as suggestions awaiting operator confirmation,
#   * checks whether unattended-upgrades is enabled,
#   * sweeps old audit rows,
#   * raises issues and notifies on WARNING/CRITICAL.
#
# The action logic lives in the Python engine (claudio.autonomous.maintenance),
# not here, so the timer-driven pass behaves exactly like the in-process
# scheduler and stays covered by the test suite. This script only wires it to
# systemd.
#
# Exit code mirrors the verdict:
#   0 = OK   1 = WARNING   2 = CRITICAL   3 = the pass itself errored
#
# NOTE: the internal TaskScheduler already runs maintenance on its own cron.
# This timer is an OPTIONAL belt-and-suspenders path for operators who want the
# OS to drive it. Do NOT enable both the internal maintenance schedule AND this
# timer, or maintenance will run twice. See deploy/install.sh.

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

exec "$PYTHON" -m claudio.autonomous.cli maintenance
