#!/usr/bin/env bash
# Install the nightly golden-nuggets health report timer on the VPS.
# Mirrors scripts/install_vps_oom_alert.sh. Invoked from
# scripts/deploy/remote_deploy.sh (installers there are ENUMERATED, not
# auto-discovered — a new installer that isn't wired in never runs).
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-nuggets-health-report.service"
TIMER_NAME="universal-agent-nuggets-health-report.timer"
SYSTEMD_DIR="/etc/systemd/system"

SCRIPT_SRC="$APP_ROOT/scripts/nuggets_health_report.py"
SERVICE_SRC="$APP_ROOT/deployment/systemd/$SERVICE_NAME"
TIMER_SRC="$APP_ROOT/deployment/systemd/$TIMER_NAME"

for path in "$SCRIPT_SRC" "$SERVICE_SRC" "$TIMER_SRC"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
done

chmod 0755 "$SCRIPT_SRC"
install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DIR/$SERVICE_NAME"
install -m 0644 "$TIMER_SRC" "$SYSTEMD_DIR/$TIMER_NAME"

systemctl daemon-reload
systemctl enable --now "$TIMER_NAME"

# `journalctl -u <other-unit>` needs journal read access. The report degrades to
# "did not fire" rather than crashing if this is missing, so make it best-effort
# but say so loudly — a silent permissions gap would make every report a false alarm.
if ! id -nG ua 2>/dev/null | tr ' ' '\n' | grep -qx systemd-journal; then
  usermod -aG systemd-journal ua 2>/dev/null \
    && echo "added ua to systemd-journal (takes effect for units started after this)" \
    || echo "WARN: could not add ua to systemd-journal; the report may not see the nuggets journal" >&2
fi

echo "--> $TIMER_NAME installed; next elapse:"
systemctl list-timers "$TIMER_NAME" --all --no-pager 2>/dev/null | sed -n '1,3p' || true
