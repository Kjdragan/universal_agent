#!/usr/bin/env bash
# Install + enable the end-of-day golden-nuggets demo judge systemd timer
# (Component D — once daily 23:50 America/Chicago, deploy-independent,
# SECRET-BEARING). Idempotent — safe to re-run on every deploy. Single service +
# single timer. Mirrors install_proactive_demo_build_sweep_timer.sh (validate
# units present -> install -m 0644 -> daemon-reload -> enable --now the timer).
# This is a NEW producer-invoker, not a migration — no in-process twin, so no
# double-fire gate. The cron itself is GATED OFF by default
# (UA_PROACTIVE_DEMO_NUGGETS_ENABLED) so arming the timer builds nothing until
# the operator validates + flips the flag. ROLLBACK: `systemctl disable --now
# universal-agent-proactive-demo-nuggets.timer`.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
SRC="$APP_ROOT/deployment/systemd"

UNITS=(
  "universal-agent-proactive-demo-nuggets.service"
  "universal-agent-proactive-demo-nuggets.timer"
)

# Validate every unit file is present BEFORE touching systemd.
for f in "${UNITS[@]}"; do
  if [[ ! -f "$SRC/$f" ]]; then
    echo "Missing required unit file: $SRC/$f" >&2
    exit 2
  fi
done

for f in "${UNITS[@]}"; do
  install -m 0644 "$SRC/$f" "$SYSTEMD_DIR/$f"
done

systemctl daemon-reload

# enable --now arms the TIMER (not the oneshot service). We do NOT
# `systemctl start` the service at install — the timer fires it on its slot.
systemctl enable --now "universal-agent-proactive-demo-nuggets.timer"

echo "== proactive demo golden-nuggets timer =="
systemctl list-timers "universal-agent-proactive-demo-nuggets.timer" --all --no-pager || true
