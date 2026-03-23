#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_vps_systemd_units.sh --lane <production|staging> --app-root <path> [options]

Options:
  --app-user <user>       Service user. Default: ua
  --systemd-dir <path>    Unit output directory. Default: /etc/systemd/system
  --no-reload             Skip systemctl daemon-reload/enable
EOF
}

APP_ROOT=""
APP_USER="ua"
LANE=""
SYSTEMD_DIR="/etc/systemd/system"
NO_RELOAD="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-root)
      APP_ROOT="${2:-}"
      shift 2
      ;;
    --app-user)
      APP_USER="${2:-}"
      shift 2
      ;;
    --lane)
      LANE="${2:-}"
      shift 2
      ;;
    --systemd-dir)
      SYSTEMD_DIR="${2:-}"
      shift 2
      ;;
    --no-reload)
      NO_RELOAD="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$APP_ROOT" || -z "$LANE" ]]; then
  usage >&2
  exit 2
fi

if [[ ! -d "$APP_ROOT" ]]; then
  echo "App root does not exist: $APP_ROOT" >&2
  exit 3
fi

case "$LANE" in
  production|staging)
    ;;
  *)
    echo "Lane must be 'production' or 'staging'. Got: $LANE" >&2
    exit 4
    ;;
esac

APP_ROOT="$(cd "$APP_ROOT" && pwd)"
SYSTEMD_DIR="${SYSTEMD_DIR%/}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DIR="$REPO_ROOT/deployment/systemd/templates"
STACK_LIMIT_CONF="$REPO_ROOT/deployment/systemd/universal-agent-stack-limit.conf"

if [[ ! -d "$TEMPLATE_DIR" ]]; then
  echo "Template directory missing: $TEMPLATE_DIR" >&2
  exit 5
fi

if [[ ! -f "$STACK_LIMIT_CONF" ]]; then
  echo "Stack limit drop-in missing: $STACK_LIMIT_CONF" >&2
  exit 6
fi

mkdir -p "$SYSTEMD_DIR"

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'
}

render_template() {
  local template_path="$1"
  local output_path="$2"
  local description="$3"
  local webui_port="${4:-}"
  local gateway_unit="${5:-}"
  local tmp_file
  tmp_file="$(mktemp)"
  sed \
    -e "s|__APP_ROOT__|$(escape_sed_replacement "$APP_ROOT")|g" \
    -e "s|__APP_USER__|$(escape_sed_replacement "$APP_USER")|g" \
    -e "s|__UNIT_DESCRIPTION__|$(escape_sed_replacement "$description")|g" \
    -e "s|__WEBUI_PORT__|$(escape_sed_replacement "$webui_port")|g" \
    -e "s|__GATEWAY_UNIT__|$(escape_sed_replacement "$gateway_unit")|g" \
    "$template_path" >"$tmp_file"
  install -m 0644 "$tmp_file" "$output_path"
  rm -f "$tmp_file"
  echo "Installed unit: $output_path"
}

install_stack_limit_dropin() {
  local unit_name="$1"
  local dropin_dir="$SYSTEMD_DIR/${unit_name}.d"
  mkdir -p "$dropin_dir"
  install -m 0644 "$STACK_LIMIT_CONF" "$dropin_dir/stack-limit.conf"
  echo "Installed drop-in: $dropin_dir/stack-limit.conf"
}

declare -a units_to_enable=()

if [[ "$LANE" == "production" ]]; then
  render_template \
    "$TEMPLATE_DIR/universal-agent-gateway.service.template" \
    "$SYSTEMD_DIR/universal-agent-gateway.service" \
    "Universal Agent Gateway"
  render_template \
    "$TEMPLATE_DIR/universal-agent-api.service.template" \
    "$SYSTEMD_DIR/universal-agent-api.service" \
    "Universal Agent API"
  render_template \
    "$TEMPLATE_DIR/universal-agent-webui.service.template" \
    "$SYSTEMD_DIR/universal-agent-webui.service" \
    "Universal Agent Web UI" \
    "3000"
  render_template \
    "$TEMPLATE_DIR/universal-agent-telegram.service.template" \
    "$SYSTEMD_DIR/universal-agent-telegram.service" \
    "Universal Agent Telegram Poller" \
    "" \
    "universal-agent-gateway.service"
  install_stack_limit_dropin "universal-agent-gateway.service"
  install_stack_limit_dropin "universal-agent-api.service"
  units_to_enable=(
    "universal-agent-gateway.service"
    "universal-agent-api.service"
    "universal-agent-webui.service"
    "universal-agent-telegram.service"
  )
else
  render_template \
    "$TEMPLATE_DIR/universal-agent-gateway.service.template" \
    "$SYSTEMD_DIR/universal-agent-staging-gateway.service" \
    "Universal Agent Staging Gateway"
  render_template \
    "$TEMPLATE_DIR/universal-agent-api.service.template" \
    "$SYSTEMD_DIR/universal-agent-staging-api.service" \
    "Universal Agent Staging API"
  render_template \
    "$TEMPLATE_DIR/universal-agent-webui.service.template" \
    "$SYSTEMD_DIR/universal-agent-staging-webui.service" \
    "Universal Agent Staging Web UI" \
    "3001"
  install_stack_limit_dropin "universal-agent-staging-gateway.service"
  install_stack_limit_dropin "universal-agent-staging-api.service"
  units_to_enable=(
    "universal-agent-staging-gateway.service"
    "universal-agent-staging-api.service"
    "universal-agent-staging-webui.service"
  )
fi

if [[ "$NO_RELOAD" == "true" ]]; then
  exit 0
fi

if [[ "$SYSTEMD_DIR" != "/etc/systemd/system" ]]; then
  echo "Skipping systemctl operations for non-default systemd dir: $SYSTEMD_DIR"
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not available; units copied but not enabled/reloaded" >&2
  exit 0
fi

systemctl daemon-reload
systemctl enable "${units_to_enable[@]}"
