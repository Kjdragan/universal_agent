#!/usr/bin/env bash
# Install + enable the daily arXiv local-index harvest systemd timer.
# Idempotent — safe to re-run on every deploy. Mirrors
# scripts/install_vps_session_reaper_timer.sh (the no-secrets maintenance
# pattern). The harvest keeps ~/.arxiv-local-index/arxiv_index.db fresh so
# paper_to_podcast discovery makes ZERO live arXiv API calls (2026-07-10
# HTTP-429 RCA — see services/arxiv_local_index.py).
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-arxiv-index-harvest.service"
TIMER_NAME="universal-agent-arxiv-index-harvest.timer"
SYSTEMD_DIR="/etc/systemd/system"

SERVICE_SRC="$APP_ROOT/deployment/systemd/$SERVICE_NAME"
TIMER_SRC="$APP_ROOT/deployment/systemd/$TIMER_NAME"
MODULE_SRC="$APP_ROOT/src/universal_agent/services/arxiv_local_index.py"

for path in "$SERVICE_SRC" "$TIMER_SRC" "$MODULE_SRC"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
done

install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DIR/$SERVICE_NAME"
install -m 0644 "$TIMER_SRC" "$SYSTEMD_DIR/$TIMER_NAME"

systemctl daemon-reload
# enable --now starts the TIMER (not the harvest service). The first harvest
# fires at the next 04:40 CT slot; the one-time 12-month backfill is a manual
# operator/agent step (harvest --backfill-months 12), not part of install.
systemctl enable --now "$TIMER_NAME"

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 10 || true
echo
systemctl list-timers "$TIMER_NAME" --no-pager || true
