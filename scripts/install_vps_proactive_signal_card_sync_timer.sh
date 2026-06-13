#!/usr/bin/env bash
# Install + enable the hourly proactive-signal-card-sync systemd timer.
# Idempotent — safe to re-run on every deploy. Mirrors
# scripts/install_uv_cache_prune_timer.sh.
#
# The timer runs scripts/proactive_signal_card_sync.py hourly (24/7 at the timer
# level, dormancy-gated to the active window in the script) as a
# deploy-INDEPENDENT autonomous generator of proactive_signal_cards from the CSI
# feedstock — so the card list stays fresh without anyone opening the dashboard.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-proactive-signal-card-sync.service"
TIMER_NAME="universal-agent-proactive-signal-card-sync.timer"
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

systemctl daemon-reload
# enable --now starts the TIMER (not the sync service) — there is no need to
# force an immediate card sync at install; the next :25 slot will fire.
systemctl enable --now "$TIMER_NAME"

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 10 || true
echo
systemctl list-timers "$TIMER_NAME" --no-pager || true
