#!/usr/bin/env bash
# Install + enable the daily uv-cache-prune systemd timer. Idempotent — safe to
# re-run on every deploy. Mirrors scripts/install_vps_service_watchdog.sh.
# The timer runs scripts/prune_uv_caches.sh daily as a deploy-INDEPENDENT
# backstop against disk growth (the deploy itself also prunes inline).
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-uv-cache-prune.service"
TIMER_NAME="universal-agent-uv-cache-prune.timer"
SYSTEMD_DIR="/etc/systemd/system"

SCRIPT_SRC="$APP_ROOT/scripts/prune_uv_caches.sh"
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
# enable --now starts the TIMER (not the prune service) — the deploy already
# pruned inline, so there is no need to force an immediate prune at install.
systemctl enable --now "$TIMER_NAME"

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 10 || true
echo
systemctl list-timers "$TIMER_NAME" --no-pager || true
