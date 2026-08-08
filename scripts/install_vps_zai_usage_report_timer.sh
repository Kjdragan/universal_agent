#!/usr/bin/env bash
# Install + enable the weekly ZAI usage report timer.
#
#   universal-agent-zai-usage-report — Wed 09:00 CT: deterministic 5-lane token
#   consolidation (this week vs prior) + weekly-budget-meter state, emailed to
#   the operator. Standing follow-up to the 2026-08-08 ZAI usage audit; first
#   firing 2026-08-12 per the operator's "re-look in four days" request.
#
# No in-process gateway twin (the module is not registered in gateway_server),
# so there is no double-fire gate to coordinate. The module self-bootstraps
# secrets via initialize_runtime_secrets().
#
# Idempotent — safe to re-run on every deploy.
# ROLLBACK: systemctl disable --now universal-agent-zai-usage-report.timer
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
SRC="$APP_ROOT/deployment/systemd"

UNITS=(
  "universal-agent-zai-usage-report.service"
  "universal-agent-zai-usage-report.timer"
)

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
systemctl enable --now universal-agent-zai-usage-report.timer

echo "== ZAI usage report timer =="
systemctl list-timers universal-agent-zai-usage-report.timer --all --no-pager || true
