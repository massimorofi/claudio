#!/usr/bin/env bash
# Claudio Phase 1 deployment — operator-run installer.
#
# Sets up the host to run Claudio as a managed service with a scheduled health
# check. Run it yourself after reading it; it makes system-level changes and is
# NOT run automatically.
#
#   sudo ./install.sh
#
# What it does (idempotent — safe to re-run):
#   1. Creates the dedicated 'claudio' system user + group.
#   2. Installs the code into /opt/claudio (override with INSTALL_DIR=...).
#   3. Creates the Python venv and installs backend requirements.
#   4. Installs the scoped sudoers file (validated with visudo).
#   5. Installs the systemd units (claudio.service, claudio-health.{service,timer}).
#   6. Enables and starts the backend service + the health timer.
#
# It does NOT enable any mutating sudo rules by default — Phase 1 is report-only.
# Review deploy/sudoers.d-claudio and uncomment entries as you reach later phases.

set -euo pipefail

# ---- configuration ----------------------------------------------------

INSTALL_DIR="${INSTALL_DIR:-/opt/claudio}"
SERVICE_USER="claudio"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root (parent of deploy/)
DEPLOY_DIR="$REPO_DIR/deploy"

# ---- preflight --------------------------------------------------------

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (sudo)." >&2
    exit 1
fi

if [ ! -d "$REPO_DIR/backend/src/claudio" ]; then
    echo "ERROR: backend source not found at $REPO_DIR/backend/src/claudio" >&2
    exit 1
fi

echo "Claudio Phase 1 installer"
echo "  source repo : $REPO_DIR"
echo "  install dir : $INSTALL_DIR"
echo
read -r -p "Proceed? [y/N] " REPLY
case "$REPLY" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted."; exit 0 ;;
esac

# ---- 1. dedicated user ------------------------------------------------

if ! getent group "$SERVICE_USER" >/dev/null 2>&1; then
    groupadd --system "$SERVICE_USER"
    echo "Created group $SERVICE_USER"
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/claudio" \
        --shell /usr/sbin/nologin --gid "$SERVICE_USER" "$SERVICE_USER"
    echo "Created system user $SERVICE_USER"
else
    echo "User $SERVICE_USER already exists"
fi

# ---- 2. install code --------------------------------------------------

mkdir -p "$INSTALL_DIR"
echo "Syncing code into $INSTALL_DIR ..."
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude '.venv' \
        --exclude 'data' \
        --exclude '__pycache__' \
        --exclude 'node_modules' \
        --exclude '.git' \
        "$REPO_DIR/" "$INSTALL_DIR/"
else
    # Fallback without rsync: copy the pieces we need.
    cp -a "$REPO_DIR/backend" "$INSTALL_DIR/"
    cp -a "$REPO_DIR/scripts" "$INSTALL_DIR/" 2>/dev/null || true
fi

# Ensure the daemon scripts are in place and executable. The systemd units call
# /opt/claudio/scripts/run_*.sh; the sources live in deploy/.
mkdir -p "$INSTALL_DIR/scripts"
install -m 0755 "$DEPLOY_DIR/run_health_check.sh" "$INSTALL_DIR/scripts/run_health_check.sh"
install -m 0755 "$DEPLOY_DIR/run_maintenance.sh" "$INSTALL_DIR/scripts/run_maintenance.sh"
install -m 0755 "$DEPLOY_DIR/run_recovery.sh" "$INSTALL_DIR/scripts/run_recovery.sh"

# Provide a config file if none exists yet.
if [ ! -f "$INSTALL_DIR/backend/config/config.yaml" ]; then
    cp "$INSTALL_DIR/backend/config/config.example.yaml" \
       "$INSTALL_DIR/backend/config/config.yaml"
    echo "Created config.yaml from the example (edit it before enabling notifications)."
fi

# ---- 3. venv + dependencies ------------------------------------------

if [ ! -x "$INSTALL_DIR/backend/.venv/bin/python" ]; then
    echo "Creating virtualenv and installing dependencies (this can take a few minutes)..."
    python3 -m venv "$INSTALL_DIR/backend/.venv"
    "$INSTALL_DIR/backend/.venv/bin/pip" install --quiet --upgrade pip
    "$INSTALL_DIR/backend/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/backend/requirements.txt"
else
    echo "Virtualenv already present"
fi

# ---- 4. sudoers -------------------------------------------------------

echo "Installing scoped sudoers..."
TMP_SUDOERS="$(mktemp)"
cp "$DEPLOY_DIR/sudoers.d-claudio" "$TMP_SUDOERS"
if visudo -cf "$TMP_SUDOERS" >/dev/null 2>&1; then
    install -m 0440 "$TMP_SUDOERS" /etc/sudoers.d/claudio
    echo "Installed /etc/sudoers.d/claudio (validated)"
else
    echo "ERROR: sudoers file failed validation. Not installed." >&2
    rm -f "$TMP_SUDOERS"
    exit 1
fi
rm -f "$TMP_SUDOERS"

# ---- 5. systemd units -------------------------------------------------

echo "Installing systemd units..."
install -m 0644 "$DEPLOY_DIR/claudio.service"          /etc/systemd/system/claudio.service
install -m 0644 "$DEPLOY_DIR/claudio-health.service"   /etc/systemd/system/claudio-health.service
install -m 0644 "$DEPLOY_DIR/claudio-health.timer"     /etc/systemd/system/claudio-health.timer
install -m 0644 "$DEPLOY_DIR/claudio-maintenance.service" /etc/systemd/system/claudio-maintenance.service
install -m 0644 "$DEPLOY_DIR/claudio-maintenance.timer"   /etc/systemd/system/claudio-maintenance.timer
install -m 0644 "$DEPLOY_DIR/claudio-recovery.service"    /etc/systemd/system/claudio-recovery.service
systemctl daemon-reload

# ---- 6. enable + start ------------------------------------------------

systemctl enable --now claudio.service
systemctl enable --now claudio-health.timer

# Boot-time disaster recovery is enabled: it should run even if the backend is
# down, and its actions are safe/reversible (restart allowlisted units, clean a
# full disk, alert on FS errors).
systemctl enable claudio-recovery.service

# The maintenance TIMER is intentionally NOT enabled. The backend's internal
# TaskScheduler already runs maintenance on autonomous.maintenance.schedule.
# Enabling this timer as well would run maintenance twice. Only enable it if
# you have disabled the internal maintenance schedule and want systemd to own it:
#   systemctl enable --now claudio-maintenance.timer

echo
echo "Done. Next steps:"
echo "  1. Edit $INSTALL_DIR/backend/config/config.yaml"
echo "     - set server.auth_token"
echo "     - configure autonomous.notification.email.* and set enabled: true"
echo "     - set autonomous.enabled / report_only, and the maintenance + recovery blocks"
echo "  2. Review /etc/sudoers.d/claudio and grant only what you have reviewed."
echo "     Regenerate the matching set: python -m claudio.autonomous.cli sudoers"
echo "  3. Reload config in the running service (or restart it):"
echo "       sudo systemctl restart claudio"
echo "  4. Check status:"
echo "       systemctl status claudio claudio-health.timer claudio-recovery.service"
echo "       journalctl -u claudio -f"
echo "  5. Open the cockpit Autonomous view (Dashboard / Issues / Tasks / Audit / Daemon)."
