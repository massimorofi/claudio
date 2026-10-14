#!/usr/bin/env bash
# Restart the Claudio backend.
set -euo pipefail
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$BACKEND_DIR/stop.sh"
"$BACKEND_DIR/start.sh"
