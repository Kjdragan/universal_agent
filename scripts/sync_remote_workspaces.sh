#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

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
DEFAULT_REMOTE_TOGGLE_PATH="${UA_REMOTE_SYNC_TOGGLE_PATH:-/api/v1/ops/remote-sync}"

print_usage() {
  cat <<'USAGE'
Usage:
  scripts/sync_remote_workspaces.sh [options]

Purpose:
  Mirror remote AGENT_RUN_WORKSPACES and durable artifacts into local directories
  for debugging.

Options:
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
  --interval <seconds>       Sync loop interval (continuous mode only).
  --ssh-port <port>          SSH port for rsync transport.
  --ssh-key <path>           Optional SSH private key path.
  --session-id <id>          Mirror only one session/cron workspace directory.
  --include-runtime-db       Include runtime_state.db and sidecar files.
  --delete-remote-after-sync Delete remote workspace immediately after successful sync.
  --prune-remote-when-local-missing
                             Delete remote workspace if it was synced before but
                             the local mirrored workspace no longer exists.
  --prune-min-age-seconds <seconds>
                             In prune mode, only delete remote workspaces older
                             than this age (default: 300).
  --allow-remote-delete      Required confirmation flag for any remote deletion mode.
  --respect-remote-toggle    Skip sync cycle unless remote toggle is enabled.
  --gateway-url <url>        Gateway base URL for remote toggle check.
  --ops-token <token>        Ops token for remote toggle check (falls back to env/.env).
  --remote-toggle-path <path>Remote toggle endpoint path.
  --no-delete                Do not delete files removed on remote side.
  --once                     Run one sync then exit.
  --help                     Print help.

Examples:
  scripts/sync_remote_workspaces.sh --once
  scripts/sync_remote_workspaces.sh --interval 20
  scripts/sync_remote_workspaces.sh --session-id cron_030a3bd24c --once
USAGE
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

warn() {
  printf '[%s] WARNING: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
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

assert_nonnegative_integer() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    echo "${label} must be a non-negative integer. Got: ${value}" >&2
    exit 1
  fi
}

validate_workspace_id() {
  local workspace_id="$1"
  [[ "${workspace_id}" =~ ^[A-Za-z0-9._-]+$ ]]
}

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
RUN_ONCE="false"
INCLUDE_RUNTIME_DB="false"
DELETE_MODE="true"
SKIP_SYNCED="true"
DELETE_REMOTE_AFTER_SYNC="false"
PRUNE_REMOTE_WHEN_LOCAL_MISSING="false"
ALLOW_REMOTE_DELETE="false"
PRUNE_MIN_AGE_SEC="${DEFAULT_PRUNE_MIN_AGE_SEC}"
RESPECT_REMOTE_TOGGLE="false"
GATEWAY_URL="${DEFAULT_GATEWAY_URL}"
OPS_TOKEN="${UA_OPS_TOKEN:-}"
REMOTE_TOGGLE_PATH="${DEFAULT_REMOTE_TOGGLE_PATH}"
INCLUDE_ARTIFACTS_SYNC="${UA_REMOTE_SYNC_INCLUDE_ARTIFACTS:-true}"

while [[ $# -gt 0 ]]; do
  case "$1" in
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
    --remote-toggle-path)
      REMOTE_TOGGLE_PATH="${2:-}"
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
    --once)
      RUN_ONCE="true"
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
require_command rsync
require_command ssh

if [[ -n "${SSH_KEY}" && ! -f "${SSH_KEY}" ]]; then
  echo "SSH key does not exist: ${SSH_KEY}" >&2
  exit 1
fi

if [[ "${ALLOW_REMOTE_DELETE}" != "true" ]] && \
   ([[ "${DELETE_REMOTE_AFTER_SYNC}" == "true" ]] || [[ "${PRUNE_REMOTE_WHEN_LOCAL_MISSING}" == "true" ]]); then
  echo "Remote deletion mode requested, but --allow-remote-delete was not provided." >&2
  exit 1
fi

if [[ "${RESPECT_REMOTE_TOGGLE}" == "true" ]] && [[ -z "${GATEWAY_URL}" ]]; then
  echo "--gateway-url is required when --respect-remote-toggle is enabled." >&2
  exit 1
fi

mkdir -p "${LOCAL_DIR}"
if [[ "${INCLUDE_ARTIFACTS_SYNC}" == "true" ]]; then
  mkdir -p "${LOCAL_ARTIFACTS_DIR}"
fi
mkdir -p "$(dirname "${MANIFEST_FILE}")"
touch "${MANIFEST_FILE}"

ssh_parts=(
  ssh
  -p "${SSH_PORT}"
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=accept-new
)
if [[ -n "${SSH_KEY}" ]]; then
  ssh_parts+=(-i "${SSH_KEY}")
fi
ssh_command="$(printf '%q ' "${ssh_parts[@]}")"
remote_dir_q="$(printf '%q' "${REMOTE_DIR}")"

rsync_common_args=(
  --archive
  --compress
  --human-readable
  --partial
  --timeout=60
  -e "${ssh_command}"
)

workspace_rsync_args=("${rsync_common_args[@]}")
artifact_rsync_args=("${rsync_common_args[@]}")

if [[ "${DELETE_MODE}" == "true" ]]; then
  workspace_rsync_args+=(--delete --delete-excluded)
  artifact_rsync_args+=(--delete --delete-excluded)
fi

if [[ "${INCLUDE_RUNTIME_DB}" != "true" ]]; then
  workspace_rsync_args+=(
    --exclude runtime_state.db
    --exclude runtime_state.db-shm
    --exclude runtime_state.db-wal
  )
fi

manifest_contains() {
  local workspace_id="$1"
  grep -Fqx -- "${workspace_id}" "${MANIFEST_FILE}"
}

mark_synced() {
  local workspace_id="$1"
  if ! manifest_contains "${workspace_id}"; then
    printf '%s\n' "${workspace_id}" >> "${MANIFEST_FILE}"
  fi
}

resolve_ops_token() {
  if [[ -n "${OPS_TOKEN}" ]]; then
    printf '%s' "${OPS_TOKEN}"
    return 0
  fi

  if [[ -n "${UA_OPS_TOKEN:-}" ]]; then
    printf '%s' "${UA_OPS_TOKEN}"
    return 0
  fi

  local env_file="${REPO_ROOT}/.env"
  if [[ -f "${env_file}" ]]; then
    local line
    line="$(grep -E '^[[:space:]]*UA_OPS_TOKEN=' "${env_file}" | tail -n 1 || true)"
    if [[ -n "${line}" ]]; then
      local value="${line#*=}"
      value="${value%\"}"
      value="${value#\"}"
      value="${value%\'}"
      value="${value#\'}"
      printf '%s' "${value}"
      return 0
    fi
  fi

  printf ''
}

remote_toggle_enabled() {
  if [[ "${RESPECT_REMOTE_TOGGLE}" != "true" ]]; then
    return 0
  fi

  require_command curl
  local token
  token="$(resolve_ops_token)"
  if [[ -z "${token}" ]]; then
    warn "Remote toggle check enabled but UA_OPS_TOKEN is missing; skipping sync cycle."
    return 1
  fi

  local url="${GATEWAY_URL%/}${REMOTE_TOGGLE_PATH}"
  local response
  if ! response="$(curl --silent --show-error --fail --max-time 15 \
      -H "x-ua-ops-token: ${token}" \
      -H "authorization: Bearer ${token}" \
      "${url}")"; then
    warn "Remote toggle check failed; skipping sync cycle."
    return 1
  fi

  if printf '%s' "${response}" | grep -Eq '"enabled"[[:space:]]*:[[:space:]]*true'; then
    return 0
  fi
  log "Remote toggle is OFF; skipping sync cycle."
  return 1
}

remote_exec() {
  local command="$1"
  "${ssh_parts[@]}" "${REMOTE_HOST}" "${command}" </dev/null
}

list_remote_workspaces() {
  if [[ -n "${SESSION_ID}" ]]; then
    printf '%s\n' "${SESSION_ID}"
    return 0
  fi
  local list_command
  list_command="cd ${remote_dir_q} && find . -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' | sort"
  remote_exec "${list_command}"
}

remote_workspace_exists() {
  local workspace_id="$1"
  local workspace_id_q
  workspace_id_q="$(printf '%q' "${workspace_id}")"
  local test_command
  test_command="test -d ${remote_dir_q}/${workspace_id_q}"
  remote_exec "${test_command}" >/dev/null 2>&1
}

remote_workspace_old_enough_for_prune() {
  local workspace_id="$1"
  local workspace_id_q
  workspace_id_q="$(printf '%q' "${workspace_id}")"
  local age_check_command
  age_check_command="now=\$(date +%s); mtime=\$(stat -c %Y ${remote_dir_q}/${workspace_id_q} 2>/dev/null || echo 0); age=\$((now-mtime)); [ \"\$age\" -ge ${PRUNE_MIN_AGE_SEC} ]"
  remote_exec "${age_check_command}" >/dev/null 2>&1
}

delete_remote_workspace() {
  local workspace_id="$1"
  if [[ "${ALLOW_REMOTE_DELETE}" != "true" ]]; then
    return 0
  fi
  if ! validate_workspace_id "${workspace_id}"; then
    warn "Skipping remote delete for invalid workspace ID: ${workspace_id}"
    return 0
  fi
  local workspace_id_q
  workspace_id_q="$(printf '%q' "${workspace_id}")"
  local delete_command
  delete_command="if test -d ${remote_dir_q}/${workspace_id_q}; then rm -rf -- ${remote_dir_q}/${workspace_id_q}; fi"
  if remote_exec "${delete_command}"; then
    log "Deleted remote workspace: ${workspace_id}"
  else
    warn "Failed to delete remote workspace: ${workspace_id}"
  fi
}

sync_workspace() {
  local workspace_id="$1"
  if ! validate_workspace_id "${workspace_id}"; then
    warn "Skipping invalid workspace ID from remote listing: ${workspace_id}"
    return 0
  fi

  if [[ "${SKIP_SYNCED}" == "true" ]] && manifest_contains "${workspace_id}"; then
    log "Skipping already-synced workspace: ${workspace_id}"
    return 0
  fi

  local remote_source="${REMOTE_HOST}:${REMOTE_DIR%/}/${workspace_id}/"
  local local_target="${LOCAL_DIR%/}/${workspace_id}/"
  mkdir -p "${local_target}"

  if rsync "${workspace_rsync_args[@]}" "${remote_source}" "${local_target}"; then
    log "Synced workspace: ${workspace_id}"
    mark_synced "${workspace_id}"
    if [[ "${DELETE_REMOTE_AFTER_SYNC}" == "true" ]]; then
      delete_remote_workspace "${workspace_id}"
    fi
    return 0
  fi

  warn "Sync failed for workspace: ${workspace_id}"
  return 1
}

sync_artifacts() {
  if [[ "${INCLUDE_ARTIFACTS_SYNC}" != "true" ]]; then
    return 0
  fi

  local remote_source="${REMOTE_HOST}:${REMOTE_ARTIFACTS_DIR%/}/"
  local local_target="${LOCAL_ARTIFACTS_DIR%/}/"
  mkdir -p "${local_target}"

  if rsync "${artifact_rsync_args[@]}" "${remote_source}" "${local_target}"; then
    log "Synced durable artifacts: ${REMOTE_ARTIFACTS_DIR}"
    return 0
  fi

  warn "Sync failed for durable artifacts: ${REMOTE_ARTIFACTS_DIR}"
  return 1
}

prune_remote_workspaces_missing_locally() {
  if [[ "${PRUNE_REMOTE_WHEN_LOCAL_MISSING}" != "true" ]]; then
    return 0
  fi

  while IFS= read -r workspace_id; do
    [[ -z "${workspace_id}" ]] && continue
    if [[ ! -d "${LOCAL_DIR%/}/${workspace_id}" ]]; then
      if remote_workspace_exists "${workspace_id}"; then
        if remote_workspace_old_enough_for_prune "${workspace_id}"; then
          delete_remote_workspace "${workspace_id}"
        else
          log "Skipping prune for recent remote workspace (<${PRUNE_MIN_AGE_SEC}s): ${workspace_id}"
        fi
      fi
    fi
  done < "${MANIFEST_FILE}"
}

sync_once() {
  if ! remote_toggle_enabled; then
    return 0
  fi

  local failed="0"
  if ! sync_artifacts; then
    failed="1"
  fi
  while IFS= read -r workspace_id; do
    [[ -z "${workspace_id}" ]] && continue
    if ! sync_workspace "${workspace_id}"; then
      failed="1"
    fi
  done < <(list_remote_workspaces)

  prune_remote_workspaces_missing_locally

  if [[ "${failed}" == "1" ]]; then
    return 1
  fi
  return 0
}

if [[ "${RUN_ONCE}" == "true" ]]; then
  sync_once
  exit $?
fi

log "Starting continuous sync every ${INTERVAL_SEC}s"
log "Remote root: ${REMOTE_HOST}:${REMOTE_DIR}"
log "Local root:  ${LOCAL_DIR}"
if [[ "${INCLUDE_ARTIFACTS_SYNC}" == "true" ]]; then
  log "Artifacts remote root: ${REMOTE_HOST}:${REMOTE_ARTIFACTS_DIR}"
  log "Artifacts local root:  ${LOCAL_ARTIFACTS_DIR}"
else
  log "Artifacts sync: disabled"
fi
log "Manifest:    ${MANIFEST_FILE}"
if [[ "${SKIP_SYNCED}" == "true" ]]; then
  log "Skip mode:   enabled (already-synced workspaces are skipped)"
else
  log "Skip mode:   disabled (workspaces may re-sync)"
fi
if [[ "${DELETE_REMOTE_AFTER_SYNC}" == "true" ]]; then
  warn "Remote delete-after-sync is ENABLED"
fi
if [[ "${PRUNE_REMOTE_WHEN_LOCAL_MISSING}" == "true" ]]; then
  warn "Remote prune-when-local-missing is ENABLED (min age ${PRUNE_MIN_AGE_SEC}s)"
fi
if [[ "${RESPECT_REMOTE_TOGGLE}" == "true" ]]; then
  log "Remote toggle gating is ENABLED (${GATEWAY_URL%/}${REMOTE_TOGGLE_PATH})"
fi

while true; do
  sync_once || true
  sleep "${INTERVAL_SEC}"
done
