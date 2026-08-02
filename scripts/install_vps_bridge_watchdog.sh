#!/usr/bin/env bash
# Install + enable the desktop-bridge (SSHFS) wedge watchdog timer. Idempotent —
# safe to re-run on every deploy. Mirrors scripts/install_vps_service_watchdog.sh.
#
# Complements scripts/install_vps_desktop_bridge.sh: that installer makes a
# wedged bridge harmless to DEPLOYS (no fstab entry -> no generator stall); this
# watchdog keeps the bridge itself ALIVE, which is what the documented Path
# Guarantee depends on. Rationale and the two "never do this" rules are in
# scripts/vps_bridge_unwedge.sh.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-bridge-watchdog.service"
TIMER_NAME="universal-agent-bridge-watchdog.timer"
SYSTEMD_DIR="/etc/systemd/system"

SERVICE_SRC="$APP_ROOT/deployment/systemd/$SERVICE_NAME"
TIMER_SRC="$APP_ROOT/deployment/systemd/$TIMER_NAME"

for path in "$SERVICE_SRC" "$TIMER_SRC"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
done

install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DIR/$SERVICE_NAME"
install -m 0644 "$TIMER_SRC" "$SYSTEMD_DIR/$TIMER_NAME"
chmod 0755 "$APP_ROOT/scripts/vps_bridge_unwedge.sh" 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now "$TIMER_NAME"

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 5 || true
systemctl list-timers "$TIMER_NAME" --no-pager || true
