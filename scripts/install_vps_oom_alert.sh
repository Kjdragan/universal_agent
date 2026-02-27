#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-oom-alert.service"
TIMER_NAME="universal-agent-oom-alert.timer"
SYSTEMD_DIR="/etc/systemd/system"

SCRIPT_SRC="$APP_ROOT/scripts/watchdog_oom_notifier.py"
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

# Run a one-shot validation only when gateway health is reachable; otherwise
# the timer will execute shortly after deploy and validate then.
gateway_health_url="${UA_OOM_ALERT_GATEWAY_HEALTH_URL:-http://127.0.0.1:8002/api/v1/health}"
if command -v curl >/dev/null 2>&1; then
  if curl --silent --show-error --fail --max-time 3 "$gateway_health_url" >/dev/null; then
    systemctl start "$SERVICE_NAME" || true
  else
    echo "Skipping immediate ${SERVICE_NAME} run: gateway health check unavailable at ${gateway_health_url}."
  fi
else
  echo "Skipping immediate ${SERVICE_NAME} run: curl not installed."
fi

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 20 || true
echo
echo "== $SERVICE_NAME (last run) =="
systemctl status "$SERVICE_NAME" --no-pager -n 20 || true
