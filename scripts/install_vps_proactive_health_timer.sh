#!/usr/bin/env bash
# Install + enable the proactive-health systemd timer (S5 Phase C). Idempotent —
# safe to re-run on every deploy. Mirrors scripts/install_uv_cache_prune_timer.sh.
#
# The timer runs `python -m universal_agent.services.proactive_health_timer_main`
# every 10 min as a deploy-INDEPENDENT watchdog: it computes the proactive_health
# payload, writes the durable snapshot (activity_state.db row + JSON mirror), and
# emails the operator ONE digest on critical findings — regardless of whether any
# heartbeat ran. OnCalendar+Persistent re-arm survives the daemon-reload each
# deploy performs (the dead-timer lesson from the watchdog/oom units).
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-proactive-health.service"
TIMER_NAME="universal-agent-proactive-health.timer"
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
# enable --now starts the TIMER (not the oneshot service).
systemctl enable --now "$TIMER_NAME"

# Seed a first snapshot immediately so the heartbeat has something to read right
# after a deploy (otherwise its prompt has no proactive_health block for up to
# one timer interval). Non-fatal — the timer will produce one on its next slot.
systemctl start "$SERVICE_NAME" || echo "WARN: initial proactive-health run exited non-zero (will retry on timer)"

echo "== $TIMER_NAME =="
systemctl status "$TIMER_NAME" --no-pager -n 10 || true
echo
systemctl list-timers "$TIMER_NAME" --no-pager || true
