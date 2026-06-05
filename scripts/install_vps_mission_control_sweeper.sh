#!/usr/bin/env bash
# Install + enable the Mission Control Sweeper systemd service (S5 Phase B).
#
# Long-running Type=simple service (NOT a timer) — so we `enable --now` the unit
# itself and `restart` it on every deploy so a code change to the sweeper or its
# entrypoint takes effect immediately. State is durable in the
# __tier1_meta__/__tier2_meta__ DB sentinels, so a fast restart does not reset
# the cadence clock. Idempotent — safe to re-run on every deploy. Mirrors the
# style of scripts/install_vps_oom_alert.sh.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-mission-control-sweeper.service"
SYSTEMD_DIR="/etc/systemd/system"

SERVICE_SRC="$APP_ROOT/deployment/systemd/$SERVICE_NAME"

if [[ ! -f "$SERVICE_SRC" ]]; then
  echo "Missing required file: $SERVICE_SRC" >&2
  exit 2
fi

install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DIR/$SERVICE_NAME"

systemctl daemon-reload
# enable --now installs + starts the unit; restart guarantees a re-deploy picks
# up freshly-deployed sweeper code (the unit may already be running from a prior
# deploy). Both are no-ops on a clean first install beyond starting once.
systemctl enable --now "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME" || true

echo "== $SERVICE_NAME =="
systemctl status "$SERVICE_NAME" --no-pager -n 20 || true
