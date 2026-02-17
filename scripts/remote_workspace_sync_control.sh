#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

INSTALL_SCRIPT="${REPO_ROOT}/scripts/install_remote_workspace_sync_timer.sh"
SYNC_SCRIPT="${REPO_ROOT}/scripts/sync_remote_workspaces.sh"

DEFAULT_SERVICE_NAME="ua-remote-workspace-sync"
DEFAULT_REMOTE_HOST="${UA_REMOTE_SSH_HOST:-root@100.106.113.93}"
DEFAULT_REMOTE_DIR="${UA_REMOTE_WORKSPACES_DIR:-/opt/universal_agent/AGENT_RUN_WORKSPACES}"
DEFAULT_LOCAL_DIR="${UA_LOCAL_MIRROR_DIR:-${REPO_ROOT}/AGENT_RUN_WORKSPACES}"
DEFAULT_REMOTE_ARTIFACTS_DIR="${UA_REMOTE_ARTIFACTS_DIR:-/opt/universal_agent/artifacts}"
DEFAULT_LOCAL_ARTIFACTS_DIR="${UA_LOCAL_ARTIFACTS_MIRROR_DIR:-${REPO_ROOT}/tmp/remote_vps_artifacts}"
DEFAULT_MANIFEST_FILE="${UA_REMOTE_SYNC_MANIFEST_FILE:-${REPO_ROOT}/tmp/remote_sync_state/synced_workspaces.txt}"
DEFAULT_INTERVAL_SEC="${UA_REMOTE_SYNC_INTERVAL_SEC:-600}"
DEFAULT_SSH_PORT="${UA_REMOTE_SSH_PORT:-22}"
DEFAULT_SSH_KEY="${UA_REMOTE_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
DEFAULT_GATEWAY_URL="${UA_REMOTE_GATEWAY_URL:-https://api.clearspringcg.com}"

print_usage() {
  cat <<'USAGE'
Usage:
  scripts/remote_workspace_sync_control.sh <command> [options]

Commands:
  status      Show whether sync timer is enabled/active.
  on          Enable periodic sync timer.
  off         Disable periodic sync timer.
  toggle      Toggle timer on/off.
  sync-now    Run one one-shot sync immediately (no timer change).

Options (for on/sync-now, optional):
  --service-name <name>    systemd unit base name (default: ua-remote-workspace-sync)
  --host <user@host>       Remote SSH host.
  --remote-dir <path>      Remote AGENT_RUN_WORKSPACES path.
  --local-dir <path>       Local mirror directory.
  --remote-artifacts-dir <path>
                           Remote durable artifacts path.
  --local-artifacts-dir <path>
                           Local durable artifacts mirror path.
  --manifest-file <path>   Manifest file path.
  --interval <seconds>     Timer interval for `on` (default: 600).
  --ssh-port <port>        SSH port.
  --ssh-key <path>         SSH key path.
  --session-id <id>        Limit sync to one session workspace.
  --gateway-url <url>      Gateway base URL for remote toggle checks.
  --ops-token <token>      Optional ops token override for remote toggle checks.
  --include-runtime-db     Include runtime_state.db files.
  --no-artifacts           Disable durable artifacts sync.
  --respect-remote-toggle  Force remote toggle gating for this command.
  --ignore-remote-toggle   Disable remote toggle gating for this command.
  --no-delete              Keep local files even if removed remotely.
  --no-skip-synced         Re-sync even if workspace ID is in manifest.
  --help                   Show help.

Examples:
  scripts/remote_workspace_sync_control.sh off
  scripts/remote_workspace_sync_control.sh sync-now
  scripts/remote_workspace_sync_control.sh on --interval 600 --session-id cron_030a3bd24c
  scripts/remote_workspace_sync_control.sh toggle
USAGE
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

require_command() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Missing required command: ${name}" >&2
    exit 1
  fi
}

assert_positive_integer() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" == "0" ]]; then
    echo "${label} must be a positive integer. Got: ${value}" >&2
    exit 1
  fi
}

if [[ $# -lt 1 ]]; then
  print_usage >&2
  exit 1
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_usage
  exit 0
fi

COMMAND="$1"
shift

SERVICE_NAME="${DEFAULT_SERVICE_NAME}"
REMOTE_HOST="${DEFAULT_REMOTE_HOST}"
REMOTE_DIR="${DEFAULT_REMOTE_DIR}"
LOCAL_DIR="${DEFAULT_LOCAL_DIR}"
REMOTE_ARTIFACTS_DIR="${DEFAULT_REMOTE_ARTIFACTS_DIR}"
LOCAL_ARTIFACTS_DIR="${DEFAULT_LOCAL_ARTIFACTS_DIR}"
MANIFEST_FILE="${DEFAULT_MANIFEST_FILE}"
INTERVAL_SEC="${DEFAULT_INTERVAL_SEC}"
SSH_PORT="${DEFAULT_SSH_PORT}"
SSH_KEY="${DEFAULT_SSH_KEY}"
GATEWAY_URL="${DEFAULT_GATEWAY_URL}"
OPS_TOKEN=""
SESSION_ID=""
INCLUDE_RUNTIME_DB="false"
DELETE_MODE="true"
SKIP_SYNCED="true"
RESPECT_REMOTE_TOGGLE="auto"
INCLUDE_ARTIFACTS_SYNC="${UA_REMOTE_SYNC_INCLUDE_ARTIFACTS:-true}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name)
      SERVICE_NAME="${2:-}"
      shift 2
      ;;
    --host)
      REMOTE_HOST="${2:-}"
      shift 2
      ;;
    --remote-dir)
      REMOTE_DIR="${2:-}"
      shift 2
      ;;
    --local-dir)
      LOCAL_DIR="${2:-}"
      shift 2
      ;;
    --remote-artifacts-dir)
      REMOTE_ARTIFACTS_DIR="${2:-}"
      shift 2
      ;;
    --local-artifacts-dir)
      LOCAL_ARTIFACTS_DIR="${2:-}"
      shift 2
      ;;
    --manifest-file)
      MANIFEST_FILE="${2:-}"
      shift 2
      ;;
    --interval)
      INTERVAL_SEC="${2:-}"
      shift 2
      ;;
    --ssh-port)
      SSH_PORT="${2:-}"
      shift 2
      ;;
    --ssh-key)
      SSH_KEY="${2:-}"
      shift 2
      ;;
    --session-id)
      SESSION_ID="${2:-}"
      shift 2
      ;;
    --gateway-url)
      GATEWAY_URL="${2:-}"
      shift 2
      ;;
    --ops-token)
      OPS_TOKEN="${2:-}"
      shift 2
      ;;
    --include-runtime-db)
      INCLUDE_RUNTIME_DB="true"
      shift
      ;;
    --no-artifacts)
      INCLUDE_ARTIFACTS_SYNC="false"
      shift
      ;;
    --respect-remote-toggle)
      RESPECT_REMOTE_TOGGLE="true"
      shift
      ;;
    --ignore-remote-toggle)
      RESPECT_REMOTE_TOGGLE="false"
      shift
      ;;
    --no-delete)
      DELETE_MODE="false"
      shift
      ;;
    --no-skip-synced)
      SKIP_SYNCED="false"
      shift
      ;;
    --help|-h)
      print_usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      print_usage >&2
      exit 1
      ;;
  esac
done

assert_positive_integer "${INTERVAL_SEC}" "--interval"
assert_positive_integer "${SSH_PORT}" "--ssh-port"
require_command systemctl

if [[ "${COMMAND}" != "off" && "${COMMAND}" != "status" && -n "${SSH_KEY}" && ! -f "${SSH_KEY}" ]]; then
  echo "SSH key does not exist: ${SSH_KEY}" >&2
  exit 1
fi

timer_is_enabled() {
  systemctl --user is-enabled "${SERVICE_NAME}.timer" >/dev/null 2>&1
}

timer_is_active() {
  systemctl --user is-active "${SERVICE_NAME}.timer" >/dev/null 2>&1
}

cmd_status() {
  local enabled="no"
  local active="no"
  if timer_is_enabled; then
    enabled="yes"
  fi
  if timer_is_active; then
    active="yes"
  fi
  echo "service_name=${SERVICE_NAME}"
  echo "timer_enabled=${enabled}"
  echo "timer_active=${active}"
  systemctl --user status "${SERVICE_NAME}.timer" --no-pager || true
}

cmd_off() {
  "${INSTALL_SCRIPT}" --service-name "${SERVICE_NAME}" --uninstall
  log "Sync automation is OFF"
}

build_install_args() {
  local args=(
    --service-name "${SERVICE_NAME}"
    --host "${REMOTE_HOST}"
    --remote-dir "${REMOTE_DIR}"
    --local-dir "${LOCAL_DIR}"
    --remote-artifacts-dir "${REMOTE_ARTIFACTS_DIR}"
    --local-artifacts-dir "${LOCAL_ARTIFACTS_DIR}"
    --manifest-file "${MANIFEST_FILE}"
    --interval "${INTERVAL_SEC}"
    --ssh-port "${SSH_PORT}"
    --ssh-key "${SSH_KEY}"
  )
  if [[ -n "${SESSION_ID}" ]]; then
    args+=(--session-id "${SESSION_ID}")
  fi
  if [[ "${INCLUDE_RUNTIME_DB}" == "true" ]]; then
    args+=(--include-runtime-db)
  fi
  if [[ "${DELETE_MODE}" != "true" ]]; then
    args+=(--no-delete)
  fi
  if [[ "${SKIP_SYNCED}" != "true" ]]; then
    args+=(--no-skip-synced)
  fi
  if [[ "${INCLUDE_ARTIFACTS_SYNC}" != "true" ]]; then
    args+=(--no-artifacts)
  fi
  if [[ "${RESPECT_REMOTE_TOGGLE}" != "false" ]]; then
    args+=(--respect-remote-toggle)
    args+=(--gateway-url "${GATEWAY_URL}")
    if [[ -n "${OPS_TOKEN}" ]]; then
      args+=(--ops-token "${OPS_TOKEN}")
    fi
  fi
  printf '%s\0' "${args[@]}"
}

cmd_on() {
  local args=()
  while IFS= read -r -d '' arg; do
    args+=("${arg}")
  done < <(build_install_args)
  "${INSTALL_SCRIPT}" "${args[@]}"
  log "Sync automation is ON"
}

cmd_sync_now() {
  local args=(
    --once
    --host "${REMOTE_HOST}"
    --remote-dir "${REMOTE_DIR}"
    --local-dir "${LOCAL_DIR}"
    --remote-artifacts-dir "${REMOTE_ARTIFACTS_DIR}"
    --local-artifacts-dir "${LOCAL_ARTIFACTS_DIR}"
    --manifest-file "${MANIFEST_FILE}"
    --ssh-port "${SSH_PORT}"
    --ssh-key "${SSH_KEY}"
  )
  if [[ -n "${SESSION_ID}" ]]; then
    args+=(--session-id "${SESSION_ID}")
  fi
  if [[ "${INCLUDE_RUNTIME_DB}" == "true" ]]; then
    args+=(--include-runtime-db)
  fi
  if [[ "${DELETE_MODE}" != "true" ]]; then
    args+=(--no-delete)
  fi
  if [[ "${SKIP_SYNCED}" != "true" ]]; then
    args+=(--no-skip-synced)
  fi
  if [[ "${INCLUDE_ARTIFACTS_SYNC}" != "true" ]]; then
    args+=(--no-artifacts)
  fi
  if [[ "${RESPECT_REMOTE_TOGGLE}" == "true" ]]; then
    args+=(--respect-remote-toggle)
    args+=(--gateway-url "${GATEWAY_URL}")
    if [[ -n "${OPS_TOKEN}" ]]; then
      args+=(--ops-token "${OPS_TOKEN}")
    fi
  fi
  "${SYNC_SCRIPT}" "${args[@]}"
}

cmd_toggle() {
  if timer_is_enabled; then
    cmd_off
  else
    cmd_on
  fi
}

case "${COMMAND}" in
  status)
    cmd_status
    ;;
  on)
    cmd_on
    ;;
  off)
    cmd_off
    ;;
  toggle)
    cmd_toggle
    ;;
  sync-now)
    cmd_sync_now
    ;;
  *)
    echo "Unknown command: ${COMMAND}" >&2
    print_usage >&2
    exit 1
    ;;
esac
