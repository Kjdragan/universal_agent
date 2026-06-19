#!/usr/bin/env bash
set -Eeuo pipefail

# Install (but do NOT enable/start) the autonomous-runtime worker unit. The
# worker stays dormant until the operator cuts over: set
# UA_AUTONOMOUS_RUNTIME_MODE=split (Infisical/.env), `systemctl enable --now
# universal-agent-autonomous-runtime`, then restart the gateway so it sheds the
# loops. This installer only ships the unit file so the cutover is a config flip
# — it never enables/starts it (that would run a heavy idle gateway clone while
# the public gateway is still hosting the loops). Idempotent; preserves whatever
# enable-state a prior cutover set.

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-autonomous-runtime.service"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_SRC="$APP_ROOT/deployment/systemd/$SERVICE_NAME"

if [[ ! -f "$SERVICE_SRC" ]]; then
  echo "Missing required file: $SERVICE_SRC" >&2
  exit 2
fi

install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DIR/$SERVICE_NAME"
systemctl daemon-reload

if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
  # Already cut over (enabled). Restart for code currency.
  systemctl restart "$SERVICE_NAME" || true
  echo "== $SERVICE_NAME (enabled — restarted for code currency) =="
else
  echo "== $SERVICE_NAME installed (dormant). Cut over with:"
  echo "   1) set UA_AUTONOMOUS_RUNTIME_MODE=split in Infisical (production)"
  echo "   2) systemctl enable --now $SERVICE_NAME"
  echo "   3) systemctl restart universal-agent-gateway.service  # sheds the loops"
fi
systemctl status "$SERVICE_NAME" --no-pager -n 10 || true
