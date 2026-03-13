#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PY_BIN="${PY_BIN:-$APP_ROOT/.venv/bin/python}"
DEPLOY_PROFILE="${DEPLOY_PROFILE:-local_workstation}"
WEBUI_ENV_FILE="${WEBUI_ENV_FILE:-$APP_ROOT/web-ui/.env.local}"

if [[ ! -x "$PY_BIN" ]]; then
  echo "Python runtime not found at $PY_BIN" >&2
  exit 2
fi

if [[ ! -f "$APP_ROOT/.env" ]]; then
  echo "Bootstrap .env not found at $APP_ROOT/.env" >&2
  exit 3
fi

mkdir -p "$(dirname "$WEBUI_ENV_FILE")"
tmp_env="$(mktemp "${TMPDIR:-/tmp}/ua-local-webui-env.XXXXXX")"
trap 'rm -f "$tmp_env"' EXIT

echo "Rendering $WEBUI_ENV_FILE from Infisical-backed runtime env..."
env PYTHONPATH="$APP_ROOT/src" "$PY_BIN" \
  "$APP_ROOT/scripts/render_service_env_from_infisical.py" \
  --profile "$DEPLOY_PROFILE" \
  --include-runtime-identity \
  --output "$tmp_env" \
  --entry "UA_DASHBOARD_OPS_TOKEN=UA_DASHBOARD_OPS_TOKEN,UA_OPS_TOKEN"

install -m 600 "$tmp_env" "$WEBUI_ENV_FILE"
echo "Installed local Web UI env file: $WEBUI_ENV_FILE"
