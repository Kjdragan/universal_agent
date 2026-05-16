#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
UNIT_TEMPLATE_NAME="universal-agent-vp-worker@.service"
UNIT_TEMPLATE_SRC="$APP_ROOT/deployment/systemd/$UNIT_TEMPLATE_NAME"

if [[ ! -f "$UNIT_TEMPLATE_SRC" ]]; then
  echo "Missing VP worker unit template: $UNIT_TEMPLATE_SRC" >&2
  exit 2
fi

if [[ ! -x "$APP_ROOT/scripts/start_vp_worker.sh" ]]; then
  chmod 0755 "$APP_ROOT/scripts/start_vp_worker.sh"
fi

mkdir -p "$APP_ROOT/logs" "$APP_ROOT/AGENT_RUN_WORKSPACES"
chown -R ua:ua "$APP_ROOT/logs" "$APP_ROOT/AGENT_RUN_WORKSPACES" 2>/dev/null || true

install -m 0644 "$UNIT_TEMPLATE_SRC" "$SYSTEMD_DIR/$UNIT_TEMPLATE_NAME"

# Heartbeat during daemon-reload + enable --now so the parent SSH session sees
# periodic output and any intermediate idle-timeout-killer doesn't drop the
# connection mid-step. See docs/operations/2026-05-16_deploy_ssh_timeout_plan.md.
( while true; do printf '[heartbeat install_vp_worker_services] %s\n' "$(date -Iseconds)"; sleep 30; done ) &
HB_PID=$!
# shellcheck disable=SC2064
trap "kill $HB_PID 2>/dev/null || true; wait $HB_PID 2>/dev/null || true" EXIT

echo "--> systemctl daemon-reload..."
systemctl daemon-reload

if [[ $# -gt 0 ]]; then
  VP_IDS=("$@")
else
  VP_IDS=("vp.general.primary" "vp.coder.primary")
fi

for vp_id in "${VP_IDS[@]}"; do
  unit="universal-agent-vp-worker@${vp_id}.service"
  echo "Enabling + starting $unit"
  systemctl enable --now "$unit"
done

echo
for vp_id in "${VP_IDS[@]}"; do
  unit="universal-agent-vp-worker@${vp_id}.service"
  printf "%s=" "$unit"
  systemctl is-active "$unit" || true
done
