#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
APP_USER="${APP_USER:-ua}"
WEBUI_SERVICE="${WEBUI_SERVICE:-universal-agent-webui.service}"
WEBUI_ENV_FILE="${WEBUI_ENV_FILE:-$APP_ROOT/.env.webui}"
PY_BIN="${PY_BIN:-$APP_ROOT/.venv/bin/python3}"
DROPIN_DIR="/etc/systemd/system/${WEBUI_SERVICE}.d"
DROPIN_FILE="${DROPIN_DIR}/20-env-file.conf"

if [[ ! -x "$PY_BIN" ]]; then
  echo "Python runtime not found at $PY_BIN" >&2
  exit 2
fi

if [[ ! -f "$APP_ROOT/.env" ]]; then
  echo "Bootstrap .env not found at $APP_ROOT/.env" >&2
  exit 3
fi

echo "Rendering $WEBUI_ENV_FILE from Infisical-backed runtime env..."
runuser -u "$APP_USER" -- env PYTHONPATH="$APP_ROOT/src" "$PY_BIN" \
  "$APP_ROOT/scripts/render_service_env_from_infisical.py" \
  --profile vps \
  --output "$WEBUI_ENV_FILE" \
  --entry "UA_DASHBOARD_OPS_TOKEN=UA_DASHBOARD_OPS_TOKEN,UA_OPS_TOKEN"

if id -u "$APP_USER" >/dev/null 2>&1; then
  chown "root:${APP_USER}" "$WEBUI_ENV_FILE"
else
  chown root:root "$WEBUI_ENV_FILE"
fi
chmod 640 "$WEBUI_ENV_FILE"

mkdir -p "$DROPIN_DIR"
cat >"$DROPIN_FILE" <<EOF
[Service]
EnvironmentFile=-$WEBUI_ENV_FILE
EOF

systemctl daemon-reload
systemctl restart "$WEBUI_SERVICE"

echo "Installed webui env drop-in: $DROPIN_FILE"
systemctl show "$WEBUI_SERVICE" -p EnvironmentFiles --no-pager || true

