#!/usr/bin/env bash
# Install + enable the daily session-reaper systemd timer. Idempotent — safe to
# re-run on every deploy. Mirrors scripts/install_uv_cache_prune_timer.sh.
# The timer archives stale AGENT_RUN_WORKSPACES/{cron_*,session_*} dirs and
# then deletes AGENT_RUN_WORKSPACES_ARCHIVE entries older than 30 days,
# bounding filesystem growth from agent task workspaces.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-session-reaper.service"
TIMER_NAME="universal-agent-session-reaper.timer"
SYSTEMD_DIR="/etc/systemd/system"

REAPER_SRC="$APP_ROOT/scripts/session_reaper.sh"
CLEANUP_SRC="$APP_ROOT/scripts/archive_cleanup.py"
SERVICE_SRC="$APP_ROOT/deployment/systemd/$SERVICE_NAME"
TIMER_SRC="$APP_ROOT/deployment/systemd/$TIMER_NAME"

for path in "$REAPER_SRC" "$CLEANUP_SRC" "$SERVICE_SRC" "$TIMER_SRC"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
done

chmod 0755 "$REAPER_SRC"
install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DIR/$SERVICE_NAME"
install -m 0644 "$TIMER_SRC" "$SYSTEMD_DIR/$TIMER_NAME"

systemctl daemon-reload
# enable --now starts the TIMER (not the reaper service). The first prune fires
# at the next 03:00 CT slot; Persistent means a missed slot replays on next boot
# or after a stalled deploy window. No immediate disk hit at install time.
systemctl enable --now "$TIMER_NAME"

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 10 || true
echo
systemctl list-timers "$TIMER_NAME" --no-pager || true
