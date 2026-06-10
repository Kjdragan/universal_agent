#!/usr/bin/env bash
# Install + enable the proactive demo-build lane sweep systemd timer (3x/day,
# deploy-independent, SECRET-BEARING). Idempotent — safe to re-run on every
# deploy. Single service + single timer. Mirrors the batch-A4 installer skeleton
# (validate units present -> install -m 0644 -> daemon-reload -> enable --now the
# timer). This is a NEW producer-invoker, not a migration — no in-process twin,
# so no double-fire gate. ROLLBACK: `systemctl disable --now
# universal-agent-proactive-demo-build-sweep.timer`.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
SRC="$APP_ROOT/deployment/systemd"

UNITS=(
  "universal-agent-proactive-demo-build-sweep.service"
  "universal-agent-proactive-demo-build-sweep.timer"
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
# `systemctl start` the service at install — the timer fires it on its slots.
systemctl enable --now "universal-agent-proactive-demo-build-sweep.timer"

echo "== proactive demo-build sweep timer =="
systemctl list-timers "universal-agent-proactive-demo-build-sweep.timer" --all --no-pager || true
