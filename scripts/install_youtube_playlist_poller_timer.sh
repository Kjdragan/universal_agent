#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-youtube-playlist-poller.service"
TIMER_NAME="universal-agent-youtube-playlist-poller.timer"
SYSTEMD_DIR="/etc/systemd/system"

RUNNER_SRC="$APP_ROOT/scripts/run_youtube_playlist_poller.sh"
POLLER_SRC="$APP_ROOT/scripts/youtube_playlist_poll_to_manual_hook.py"
SERVICE_SRC="$APP_ROOT/deployment/systemd/$SERVICE_NAME"
TIMER_SRC="$APP_ROOT/deployment/systemd/$TIMER_NAME"

for path in "$RUNNER_SRC" "$POLLER_SRC" "$SERVICE_SRC" "$TIMER_SRC"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 2
  fi
done

chmod 0755 "$RUNNER_SRC" "$POLLER_SRC"
install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DIR/$SERVICE_NAME"
install -m 0644 "$TIMER_SRC" "$SYSTEMD_DIR/$TIMER_NAME"

systemctl daemon-reload
systemctl enable --now "$TIMER_NAME"
systemctl start "$SERVICE_NAME"

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 20 || true
echo
echo "== $SERVICE_NAME (last run) =="
systemctl status "$SERVICE_NAME" --no-pager -n 40 || true
echo
echo "Required env in /opt/universal_agent/.env:"
echo "  UA_YT_PLAYLIST_TRIGGER_PLAYLIST_IDS=<playlist_id>[,<playlist_id2>...]"
echo "  UA_HOOKS_TOKEN=<manual hook token>"
echo "Optional env:"
echo "  UA_YT_PLAYLIST_TRIGGER_MODE=explainer_only"
echo "  UA_YT_PLAYLIST_TRIGGER_HOOK_URL=http://127.0.0.1:8002/api/v1/hooks/youtube/manual"
echo "  UA_YT_PLAYLIST_TRIGGER_SEED_ON_FIRST_RUN=true"
