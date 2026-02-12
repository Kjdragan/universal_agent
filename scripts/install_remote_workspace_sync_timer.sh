#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYNC_SCRIPT="${REPO_ROOT}/scripts/sync_remote_workspaces.sh"

DEFAULT_SERVICE_NAME="ua-remote-workspace-sync"
DEFAULT_REMOTE_HOST="${UA_REMOTE_SSH_HOST:-root@187.77.16.29}"
DEFAULT_REMOTE_DIR="${UA_REMOTE_WORKSPACES_DIR:-/opt/universal_agent/AGENT_RUN_WORKSPACES}"
DEFAULT_LOCAL_DIR="${UA_LOCAL_MIRROR_DIR:-${REPO_ROOT}/AGENT_RUN_WORKSPACES}"
DEFAULT_REMOTE_ARTIFACTS_DIR="${UA_REMOTE_ARTIFACTS_DIR:-/opt/universal_agent/artifacts}"
DEFAULT_LOCAL_ARTIFACTS_DIR="${UA_LOCAL_ARTIFACTS_MIRROR_DIR:-${REPO_ROOT}/tmp/remote_vps_artifacts}"
DEFAULT_STATE_DIR="${UA_REMOTE_SYNC_STATE_DIR:-${REPO_ROOT}/tmp/remote_sync_state}"
DEFAULT_MANIFEST_FILE="${UA_REMOTE_SYNC_MANIFEST_FILE:-${DEFAULT_STATE_DIR}/synced_workspaces.txt}"
DEFAULT_INTERVAL_SEC="${UA_REMOTE_SYNC_INTERVAL_SEC:-30}"
DEFAULT_SSH_PORT="${UA_REMOTE_SSH_PORT:-22}"
DEFAULT_PRUNE_MIN_AGE_SEC="${UA_REMOTE_PRUNE_MIN_AGE_SEC:-300}"
DEFAULT_GATEWAY_URL="${UA_REMOTE_GATEWAY_URL:-https://api.clearspringcg.com}"

print_usage() {
  cat <<'USAGE'
Usage:
  scripts/install_remote_workspace_sync_timer.sh [options]

Purpose:
  Install a user-level systemd timer that periodically runs:
  scripts/sync_remote_workspaces.sh --once

Options:
  --service-name <name>      systemd unit base name (default: ua-remote-workspace-sync)
  --host <user@host>         Remote SSH host.
  --remote-dir <path>        Remote AGENT_RUN_WORKSPACES path.
  --local-dir <path>         Local mirror directory.
  --remote-artifacts-dir <path>
                             Remote durable artifacts path.
  --local-artifacts-dir <path>
                             Local durable artifacts mirror path.
  --no-artifacts             Disable durable artifacts sync.
  --manifest-file <path>     File that records workspace IDs already synced.
  --no-skip-synced           Disable manifest-based skip behavior.
  --interval <seconds>       Timer interval.
  --ssh-port <port>          SSH port.
  --ssh-key <path>           Optional SSH private key.
  --session-id <id>          Mirror only one session/cron workspace.
  --include-runtime-db       Include runtime_state.db and sidecar files.
  --delete-remote-after-sync Delete remote workspace after successful sync.
  --prune-remote-when-local-missing
                             Delete remote workspace when local copy is missing.
  --prune-min-age-seconds <seconds>
                             In prune mode, only delete remote workspaces older
                             than this age (default: 300).
  --allow-remote-delete      Required confirmation flag for any remote deletion mode.
  --respect-remote-toggle    Gate sync cycles behind remote Ops toggle state.
  --gateway-url <url>        Gateway base URL for remote toggle checks.
  --ops-token <token>        Optional ops token override for remote toggle checks.
  --no-delete                Do not delete files removed on remote side.
  --uninstall                Remove timer/service and exit.
  --help                     Print help.
USAGE
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

assert_positive_integer() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" == "0" ]]; then
    echo "${label} must be a positive integer. Got: ${value}" >&2
    exit 1
  fi
}

assert_nonnegative_integer() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    echo "${label} must be a non-negative integer. Got: ${value}" >&2
    exit 1
  fi
}

if [[ ! -x "${SYNC_SCRIPT}" ]]; then
  echo "Missing executable sync script: ${SYNC_SCRIPT}" >&2
  echo "Run: chmod +x scripts/sync_remote_workspaces.sh" >&2
  exit 1
fi

SERVICE_NAME="${DEFAULT_SERVICE_NAME}"
REMOTE_HOST="${DEFAULT_REMOTE_HOST}"
REMOTE_DIR="${DEFAULT_REMOTE_DIR}"
LOCAL_DIR="${DEFAULT_LOCAL_DIR}"
REMOTE_ARTIFACTS_DIR="${DEFAULT_REMOTE_ARTIFACTS_DIR}"
LOCAL_ARTIFACTS_DIR="${DEFAULT_LOCAL_ARTIFACTS_DIR}"
MANIFEST_FILE="${DEFAULT_MANIFEST_FILE}"
INTERVAL_SEC="${DEFAULT_INTERVAL_SEC}"
SSH_PORT="${DEFAULT_SSH_PORT}"
SSH_KEY=""
SESSION_ID=""
INCLUDE_RUNTIME_DB="false"
DELETE_MODE="true"
SKIP_SYNCED="true"
DELETE_REMOTE_AFTER_SYNC="false"
PRUNE_REMOTE_WHEN_LOCAL_MISSING="false"
ALLOW_REMOTE_DELETE="false"
PRUNE_MIN_AGE_SEC="${DEFAULT_PRUNE_MIN_AGE_SEC}"
RESPECT_REMOTE_TOGGLE="false"
GATEWAY_URL="${DEFAULT_GATEWAY_URL}"
OPS_TOKEN=""
UNINSTALL="false"
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
    --no-artifacts)
      INCLUDE_ARTIFACTS_SYNC="false"
      shift
      ;;
    --manifest-file)
      MANIFEST_FILE="${2:-}"
      shift 2
      ;;
    --no-skip-synced)
      SKIP_SYNCED="false"
      shift
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
    --include-runtime-db)
      INCLUDE_RUNTIME_DB="true"
      shift
      ;;
    --delete-remote-after-sync)
      DELETE_REMOTE_AFTER_SYNC="true"
      shift
      ;;
    --prune-remote-when-local-missing)
      PRUNE_REMOTE_WHEN_LOCAL_MISSING="true"
      shift
      ;;
    --allow-remote-delete)
      ALLOW_REMOTE_DELETE="true"
      shift
      ;;
    --respect-remote-toggle)
      RESPECT_REMOTE_TOGGLE="true"
      shift
      ;;
    --gateway-url)
      GATEWAY_URL="${2:-}"
      shift 2
      ;;
    --ops-token)
      OPS_TOKEN="${2:-}"
      shift 2
      ;;
    --prune-min-age-seconds)
      PRUNE_MIN_AGE_SEC="${2:-}"
      shift 2
      ;;
    --no-delete)
      DELETE_MODE="false"
      shift
      ;;
    --uninstall)
      UNINSTALL="true"
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
assert_nonnegative_integer "${PRUNE_MIN_AGE_SEC}" "--prune-min-age-seconds"

if [[ -n "${SSH_KEY}" && ! -f "${SSH_KEY}" ]]; then
  echo "SSH key does not exist: ${SSH_KEY}" >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found. Install manually with cron or run sync script in a loop." >&2
  exit 1
fi

SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}.service"
TIMER_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}.timer"

mkdir -p "${SYSTEMD_USER_DIR}"

if [[ "${UNINSTALL}" == "true" ]]; then
  systemctl --user disable --now "${SERVICE_NAME}.timer" >/dev/null 2>&1 || true
  rm -f "${SERVICE_FILE}" "${TIMER_FILE}"
  systemctl --user daemon-reload
  log "Removed ${SERVICE_NAME}.service and ${SERVICE_NAME}.timer"
  exit 0
fi

exec_start=(
  "${SYNC_SCRIPT}"
  --once
  --host "${REMOTE_HOST}"
  --remote-dir "${REMOTE_DIR}"
  --local-dir "${LOCAL_DIR}"
  --remote-artifacts-dir "${REMOTE_ARTIFACTS_DIR}"
  --local-artifacts-dir "${LOCAL_ARTIFACTS_DIR}"
  --manifest-file "${MANIFEST_FILE}"
  --interval "${INTERVAL_SEC}"
  --ssh-port "${SSH_PORT}"
)

if [[ "${SKIP_SYNCED}" != "true" ]]; then
  exec_start+=(--no-skip-synced)
fi
if [[ -n "${SSH_KEY}" ]]; then
  exec_start+=(--ssh-key "${SSH_KEY}")
fi
if [[ -n "${SESSION_ID}" ]]; then
  exec_start+=(--session-id "${SESSION_ID}")
fi
if [[ "${INCLUDE_RUNTIME_DB}" == "true" ]]; then
  exec_start+=(--include-runtime-db)
fi
if [[ "${DELETE_REMOTE_AFTER_SYNC}" == "true" ]]; then
  exec_start+=(--delete-remote-after-sync)
fi
if [[ "${PRUNE_REMOTE_WHEN_LOCAL_MISSING}" == "true" ]]; then
  exec_start+=(--prune-remote-when-local-missing)
  exec_start+=(--prune-min-age-seconds "${PRUNE_MIN_AGE_SEC}")
fi
if [[ "${ALLOW_REMOTE_DELETE}" == "true" ]]; then
  exec_start+=(--allow-remote-delete)
fi
if [[ "${RESPECT_REMOTE_TOGGLE}" == "true" ]]; then
  exec_start+=(--respect-remote-toggle)
  exec_start+=(--gateway-url "${GATEWAY_URL}")
  if [[ -n "${OPS_TOKEN}" ]]; then
    exec_start+=(--ops-token "${OPS_TOKEN}")
  fi
fi
if [[ "${DELETE_MODE}" != "true" ]]; then
  exec_start+=(--no-delete)
fi
if [[ "${INCLUDE_ARTIFACTS_SYNC}" != "true" ]]; then
  exec_start+=(--no-artifacts)
fi

exec_start_line="$(printf '%q ' "${exec_start[@]}")"

cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=Mirror remote Universal Agent workspaces to local debug mirror
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
ExecStart=${exec_start_line}
SERVICE

cat > "${TIMER_FILE}" <<TIMER
[Unit]
Description=Periodic remote workspace mirror (${SERVICE_NAME})

[Timer]
OnBootSec=20s
OnUnitActiveSec=${INTERVAL_SEC}s
AccuracySec=5s
Persistent=true
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
TIMER

systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}.timer"

log "Installed and started ${SERVICE_NAME}.timer"
log "Local mirror target: ${LOCAL_DIR}"
log "Manifest file: ${MANIFEST_FILE}"
log "Inspect status with: systemctl --user status ${SERVICE_NAME}.timer"
log "Inspect recent runs with: journalctl --user -u ${SERVICE_NAME}.service -n 80 --no-pager"
