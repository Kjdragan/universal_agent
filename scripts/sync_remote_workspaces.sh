#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEFAULT_REMOTE_HOST="${UA_REMOTE_SSH_HOST:-root@100.106.113.93}"
DEFAULT_REMOTE_DIR="${UA_REMOTE_WORKSPACES_DIR:-/opt/universal_agent/AGENT_RUN_WORKSPACES}"
DEFAULT_LOCAL_DIR="${UA_LOCAL_MIRROR_DIR:-${REPO_ROOT}/AGENT_RUN_WORKSPACES/remote_vps_workspaces}"
DEFAULT_REMOTE_ARTIFACTS_DIR="${UA_REMOTE_ARTIFACTS_DIR:-/opt/universal_agent/artifacts}"
DEFAULT_LOCAL_ARTIFACTS_DIR="${UA_LOCAL_ARTIFACTS_MIRROR_DIR:-${REPO_ROOT}/artifacts/remote_vps_artifacts}"
DEFAULT_STATE_DIR="${UA_REMOTE_SYNC_STATE_DIR:-${REPO_ROOT}/AGENT_RUN_WORKSPACES/remote_vps_sync_state}"
DEFAULT_MANIFEST_FILE="${UA_REMOTE_SYNC_MANIFEST_FILE:-${DEFAULT_STATE_DIR}/synced_workspaces.txt}"
DEFAULT_INTERVAL_SEC="${UA_REMOTE_SYNC_INTERVAL_SEC:-30}"
DEFAULT_SSH_PORT="${UA_REMOTE_SSH_PORT:-22}"
DEFAULT_SSH_AUTH_MODE="${UA_SSH_AUTH_MODE:-keys}"
DEFAULT_PRUNE_MIN_AGE_SEC="${UA_REMOTE_PRUNE_MIN_AGE_SEC:-300}"
DEFAULT_GATEWAY_URL="${UA_REMOTE_GATEWAY_URL:-https://api.clearspringcg.com}"
DEFAULT_REMOTE_TOGGLE_PATH="${UA_REMOTE_SYNC_TOGGLE_PATH:-/api/v1/ops/remote-sync}"
DEFAULT_REQUIRE_READY_MARKER="${UA_REMOTE_SYNC_REQUIRE_READY_MARKER:-true}"
DEFAULT_READY_MARKER_FILENAME="${UA_REMOTE_SYNC_READY_MARKER_FILENAME:-sync_ready.json}"
DEFAULT_READY_MIN_AGE_SEC="${UA_REMOTE_SYNC_READY_MIN_AGE_SECONDS:-45}"
DEFAULT_READY_SESSION_PREFIX="${UA_REMOTE_SYNC_READY_SESSION_PREFIX:-session_,tg_}"
DEFAULT_TAILNET_PREFLIGHT="${UA_TAILNET_PREFLIGHT:-auto}"
DEFAULT_SKIP_TAILNET_PREFLIGHT="${UA_SKIP_TAILNET_PREFLIGHT:-false}"

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
  --ssh-auth-mode <mode>     SSH auth mode: keys|tailscale_ssh (default: keys).
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
  --require-ready-marker     Only sync workspaces after remote ready marker says terminal.
  --ignore-ready-marker      Disable ready-marker gating.
  --ready-marker-name <name> Ready marker file name (default: sync_ready.json).
  --ready-min-age-seconds <seconds>
                             Minimum marker age before sync (default: 45).
  --ready-session-prefix <prefixes>
                             Comma-separated workspace prefixes for ready-marker gating
                             (default: session_,tg_).
  --no-delete                Do not delete files removed on remote side.
  --once                     Run one sync then exit.
  --status-json              Probe remote/local sync freshness and print JSON (no rsync).
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
SSH_AUTH_MODE="${DEFAULT_SSH_AUTH_MODE}"
SSH_KEY=""
SESSION_ID=""
RUN_ONCE="false"
STATUS_JSON="false"
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
REQUIRE_READY_MARKER="${DEFAULT_REQUIRE_READY_MARKER}"
READY_MARKER_FILENAME="${DEFAULT_READY_MARKER_FILENAME}"
READY_MIN_AGE_SEC="${DEFAULT_READY_MIN_AGE_SEC}"
READY_SESSION_PREFIX="${DEFAULT_READY_SESSION_PREFIX}"
TAILNET_PREFLIGHT_MODE="${DEFAULT_TAILNET_PREFLIGHT}"
SKIP_TAILNET_PREFLIGHT="${DEFAULT_SKIP_TAILNET_PREFLIGHT}"
SYNCED_WORKSPACE_LAST="false"

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
    --ssh-auth-mode)
      SSH_AUTH_MODE="${2:-}"
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
    --require-ready-marker)
      REQUIRE_READY_MARKER="true"
      shift
      ;;
    --ignore-ready-marker)
      REQUIRE_READY_MARKER="false"
      shift
      ;;
    --ready-marker-name)
      READY_MARKER_FILENAME="${2:-}"
      shift 2
      ;;
    --ready-min-age-seconds)
      READY_MIN_AGE_SEC="${2:-}"
      shift 2
      ;;
    --ready-session-prefix)
      READY_SESSION_PREFIX="${2:-}"
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
    --status-json)
      STATUS_JSON="true"
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
assert_nonnegative_integer "${READY_MIN_AGE_SEC}" "--ready-min-age-seconds"
if [[ "${STATUS_JSON}" != "true" ]]; then
  require_command rsync
fi
require_command ssh

case "$(printf '%s' "${REQUIRE_READY_MARKER}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    REQUIRE_READY_MARKER="true"
    ;;
  *)
    REQUIRE_READY_MARKER="false"
    ;;
esac

case "$(printf '%s' "${SSH_AUTH_MODE}" | tr '[:upper:]' '[:lower:]')" in
  keys)
    SSH_AUTH_MODE="keys"
    ;;
  tailscale_ssh)
    SSH_AUTH_MODE="tailscale_ssh"
    SSH_KEY=""
    ;;
  *)
    echo "--ssh-auth-mode must be one of: keys, tailscale_ssh. Got: ${SSH_AUTH_MODE}" >&2
    exit 1
    ;;
esac

if [[ -z "${READY_MARKER_FILENAME}" ]]; then
  READY_MARKER_FILENAME="${DEFAULT_READY_MARKER_FILENAME}"
fi

if [[ "${SSH_AUTH_MODE}" == "keys" && -n "${SSH_KEY}" && ! -f "${SSH_KEY}" ]]; then
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

if [[ "${STATUS_JSON}" != "true" ]]; then
  mkdir -p "${LOCAL_DIR}"
  if [[ "${INCLUDE_ARTIFACTS_SYNC}" == "true" ]]; then
    mkdir -p "${LOCAL_ARTIFACTS_DIR}"
  fi
  mkdir -p "$(dirname "${MANIFEST_FILE}")"
  touch "${MANIFEST_FILE}"
fi

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

manifest_contains_record() {
  local record="$1"
  if [[ ! -f "${MANIFEST_FILE}" ]]; then
    return 1
  fi
  grep -Fqx -- "${record}" "${MANIFEST_FILE}"
}

mark_synced_record() {
  local record="$1"
  if ! manifest_contains_record "${record}"; then
    printf '%s\n' "${record}" >> "${MANIFEST_FILE}"
  fi
}

manifest_workspace_id_from_record() {
  local record="$1"
  printf '%s' "${record%%|*}"
}

is_number() {
  local value="$1"
  [[ "${value}" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

max_number() {
  local left="$1"
  local right="$2"
  if [[ -z "${left}" ]]; then
    printf '%s' "${right}"
    return 0
  fi
  if awk -v l="${left}" -v r="${right}" 'BEGIN{exit !(r > l)}'; then
    printf '%s' "${right}"
  else
    printf '%s' "${left}"
  fi
}

epoch_from_signature() {
  local signature="$1"
  local maybe_epoch="${signature%%_*}"
  if is_number "${maybe_epoch}"; then
    printf '%s' "${maybe_epoch}"
    return 0
  fi
  printf ''
}

json_escape() {
  local input="$1"
  input="${input//\\/\\\\}"
  input="${input//\"/\\\"}"
  input="${input//$'\n'/\\n}"
  input="${input//$'\r'/\\r}"
  input="${input//$'\t'/\\t}"
  printf '%s' "${input}"
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

run_tailnet_preflight() {
  local host_only="${REMOTE_HOST#*@}"
  local tailnet_host="false"
  case "${host_only}" in
    *.tail*.ts.net|100.*) tailnet_host="true" ;;
  esac

  local should_run="false"
  case "$(printf '%s' "${TAILNET_PREFLIGHT_MODE}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|force|required) should_run="true" ;;
    0|false|no|off|disabled) should_run="false" ;;
    *) [[ "${tailnet_host}" == "true" ]] && should_run="true" ;;
  esac
  case "$(printf '%s' "${SKIP_TAILNET_PREFLIGHT}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) should_run="false" ;;
  esac

  if [[ "${should_run}" != "true" ]]; then
    return 0
  fi
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "tailscale CLI is required for tailnet preflight." >&2
    echo "Set UA_TAILNET_PREFLIGHT=off (or UA_SKIP_TAILNET_PREFLIGHT=true) for break-glass bypass." >&2
    return 1
  fi
  if ! tailscale status >/dev/null 2>&1; then
    echo "tailscale status check failed." >&2
    return 1
  fi
  if ! tailscale ping "${host_only}" >/dev/null 2>&1; then
    echo "tailscale ping failed for ${host_only}." >&2
    return 1
  fi
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

workspace_requires_ready_marker() {
  local workspace_id="$1"
  if [[ "${REQUIRE_READY_MARKER}" != "true" ]]; then
    return 1
  fi
  if [[ -z "${READY_SESSION_PREFIX}" ]]; then
    return 0
  fi
  local raw_prefix
  IFS=',' read -r -a _ready_prefixes <<< "${READY_SESSION_PREFIX}"
  for raw_prefix in "${_ready_prefixes[@]}"; do
    local prefix="${raw_prefix#"${raw_prefix%%[![:space:]]*}"}"
    prefix="${prefix%"${prefix##*[![:space:]]}"}"
    [[ -z "${prefix}" ]] && continue
    if [[ "${workspace_id}" == "${prefix}"* ]]; then
      return 0
    fi
  done
  return 1
}

remote_workspace_ready_record() {
  local workspace_id="$1"
  local workspace_id_q
  workspace_id_q="$(printf '%q' "${workspace_id}")"
  local marker_name_q
  marker_name_q="$(printf '%q' "${READY_MARKER_FILENAME}")"
  local ready_min_age_q
  ready_min_age_q="$(printf '%q' "${READY_MIN_AGE_SEC}")"
  local command
  command="
marker_path=${remote_dir_q}/${workspace_id_q}/${marker_name_q}
if ! test -f \"\${marker_path}\"; then
  legacy_run_log=${remote_dir_q}/${workspace_id_q}/run.log
  if test -s \"\${legacy_run_log}\"; then
    legacy_mtime=\$(stat -c %Y \"\${legacy_run_log}\" 2>/dev/null || echo 0)
    now_epoch=\$(date +%s)
    legacy_age=\$(awk -v now=\"\${now_epoch}\" -v completed=\"\${legacy_mtime}\" 'BEGIN{diff=now-completed; if (diff<0) diff=0; printf \"%.3f\", diff}')
    legacy_signature=\"legacy_\${legacy_mtime}_runlog\"
    if awk -v age=\"\${legacy_age}\" -v min_age=${ready_min_age_q} 'BEGIN{exit !(age < min_age)}'; then
      echo \"TOO_FRESH|\${legacy_signature}|\${legacy_age}\"
      exit 6
    fi
    echo \"READY|\${legacy_signature}|\${legacy_age}\"
    exit 0
  fi
  echo 'MISSING'
  exit 2
fi
ready_value=\$(sed -n 's/^[[:space:]]*\"ready\":[[:space:]]*\\(true\\|false\\).*/\\1/p' \"\${marker_path}\" | head -n 1)
state_value=\$(sed -n 's/^[[:space:]]*\"state\":[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' \"\${marker_path}\" | head -n 1)
completed_epoch=\$(sed -n 's/^[[:space:]]*\"completed_at_epoch\":[[:space:]]*\\([0-9.]*\\).*/\\1/p' \"\${marker_path}\" | head -n 1)
if [[ -z \"\${completed_epoch}\" ]]; then
  completed_epoch=\$(sed -n 's/^[[:space:]]*\"updated_at_epoch\":[[:space:]]*\\([0-9.]*\\).*/\\1/p' \"\${marker_path}\" | head -n 1)
fi
if [[ -z \"\${state_value}\" ]]; then
  state_value='unknown'
fi
signature=\"\${completed_epoch:-0}_\${state_value}\"
if [[ \"\${ready_value}\" != 'true' ]]; then
  echo \"NOT_READY|\${signature}\"
  exit 3
fi
case \"\${state_value}\" in
  completed|failed|timed_out|dispatch_failed|failed_pre_dispatch)
    ;;
  *)
    echo \"NOT_TERMINAL|\${signature}\"
    exit 4
    ;;
esac
if [[ -z \"\${completed_epoch}\" ]]; then
  echo \"MISSING_COMPLETED|\${signature}\"
  exit 5
fi
now_epoch=\$(date +%s)
age_seconds=\$(awk -v now=\"\${now_epoch}\" -v completed=\"\${completed_epoch}\" 'BEGIN{diff=now-completed; if (diff<0) diff=0; printf \"%.3f\", diff}')
if awk -v age=\"\${age_seconds}\" -v min_age=${ready_min_age_q} 'BEGIN{exit !(age < min_age)}'; then
  echo \"TOO_FRESH|\${signature}|\${age_seconds}\"
  exit 6
fi
echo \"READY|\${signature}|\${age_seconds}\"
"
  remote_exec "${command}"
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
  SYNCED_WORKSPACE_LAST="false"
  if ! validate_workspace_id "${workspace_id}"; then
    warn "Skipping invalid workspace ID from remote listing: ${workspace_id}"
    return 0
  fi

  local sync_record="${workspace_id}"
  if workspace_requires_ready_marker "${workspace_id}"; then
    local ready_output=""
    if ! ready_output="$(remote_workspace_ready_record "${workspace_id}" 2>/dev/null)"; then
      local reason
      reason="${ready_output%%|*}"
      case "${reason}" in
        MISSING|NOT_READY|NOT_TERMINAL|MISSING_COMPLETED)
          log "Skipping workspace until remote run is terminal: ${workspace_id} (${reason})"
          ;;
        TOO_FRESH)
          log "Skipping workspace until ready marker ages past ${READY_MIN_AGE_SEC}s: ${workspace_id}"
          ;;
        *)
          warn "Unable to evaluate ready marker for workspace: ${workspace_id} (${reason:-unknown})"
          ;;
      esac
      return 0
    fi
    local ready_signature
    ready_signature="$(printf '%s' "${ready_output}" | cut -d'|' -f2)"
    if [[ -z "${ready_signature}" ]]; then
      warn "Ready marker missing signature for workspace: ${workspace_id}"
      return 0
    fi
    sync_record="${workspace_id}|${ready_signature}"
  fi

  if [[ "${SKIP_SYNCED}" == "true" ]] && manifest_contains_record "${sync_record}"; then
    log "Skipping already-synced workspace: ${workspace_id}"
    return 0
  fi

  local remote_source="${REMOTE_HOST}:${REMOTE_DIR%/}/${workspace_id}/"
  local local_target="${LOCAL_DIR%/}/${workspace_id}/"
  mkdir -p "${local_target}"

  if rsync "${workspace_rsync_args[@]}" "${remote_source}" "${local_target}"; then
    log "Synced workspace: ${workspace_id}"
    mark_synced_record "${sync_record}"
    SYNCED_WORKSPACE_LAST="true"
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

  while IFS= read -r manifest_record; do
    [[ -z "${manifest_record}" ]] && continue
    local workspace_id
    workspace_id="$(manifest_workspace_id_from_record "${manifest_record}")"
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
  if ! run_tailnet_preflight; then
    warn "Tailnet preflight failed; skipping sync cycle."
    return 0
  fi
  if ! remote_toggle_enabled; then
    return 0
  fi

  local failed="0"
  local synced_workspace_count=0
  while IFS= read -r workspace_id; do
    [[ -z "${workspace_id}" ]] && continue
    if ! sync_workspace "${workspace_id}"; then
      failed="1"
    elif [[ "${SYNCED_WORKSPACE_LAST}" == "true" ]]; then
      synced_workspace_count=$((synced_workspace_count + 1))
    fi
  done < <(list_remote_workspaces)

  if [[ "${INCLUDE_ARTIFACTS_SYNC}" == "true" ]]; then
    if [[ "${REQUIRE_READY_MARKER}" == "true" ]] && [[ "${synced_workspace_count}" == "0" ]]; then
      log "Skipping artifact sync (no newly-completed ready workspaces in this cycle)."
    elif ! sync_artifacts; then
      failed="1"
    fi
  fi

  prune_remote_workspaces_missing_locally

  if [[ "${failed}" == "1" ]]; then
    return 1
  fi
  return 0
}

sync_status_json() {
  if ! run_tailnet_preflight; then
    printf '{"ok":false,"sync_state":"unknown","pending_ready_count":0,"error":"%s"}\n' \
      "tailnet preflight failed"
    return 1
  fi
  local pending_ready_count=0
  local ready_total_count=0
  local synced_ready_count=0
  local latest_ready_remote_epoch=""
  local latest_ready_local_epoch=""
  local latest_ready_signature=""
  local latest_ready_workspace_id=""
  local workspace_output=""

  if ! workspace_output="$(list_remote_workspaces 2>&1)"; then
    printf '{"ok":false,"sync_state":"unknown","pending_ready_count":0,"error":"%s"}\n' \
      "$(json_escape "${workspace_output}")"
    return 1
  fi

  while IFS= read -r workspace_id; do
    [[ -z "${workspace_id}" ]] && continue
    if ! validate_workspace_id "${workspace_id}"; then
      continue
    fi

    if workspace_requires_ready_marker "${workspace_id}"; then
      local ready_output=""
      ready_output="$(remote_workspace_ready_record "${workspace_id}" 2>/dev/null || true)"
      local reason="${ready_output%%|*}"
      if [[ "${reason}" != "READY" ]]; then
        continue
      fi

      local ready_signature
      ready_signature="$(printf '%s' "${ready_output}" | cut -d'|' -f2)"
      [[ -z "${ready_signature}" ]] && continue

      local sync_record="${workspace_id}|${ready_signature}"
      ready_total_count=$((ready_total_count + 1))
      if manifest_contains_record "${sync_record}"; then
        synced_ready_count=$((synced_ready_count + 1))
      else
        pending_ready_count=$((pending_ready_count + 1))
      fi

      local ready_epoch
      ready_epoch="$(epoch_from_signature "${ready_signature}")"
      if [[ -n "${ready_epoch}" ]]; then
        if [[ -z "${latest_ready_remote_epoch}" ]]; then
          latest_ready_remote_epoch="${ready_epoch}"
          latest_ready_signature="${ready_signature}"
          latest_ready_workspace_id="${workspace_id}"
        else
          local next_max
          next_max="$(max_number "${latest_ready_remote_epoch}" "${ready_epoch}")"
          if [[ "${next_max}" == "${ready_epoch}" ]]; then
            latest_ready_remote_epoch="${ready_epoch}"
            latest_ready_signature="${ready_signature}"
            latest_ready_workspace_id="${workspace_id}"
          fi
        fi
      fi
    else
      local sync_record="${workspace_id}"
      ready_total_count=$((ready_total_count + 1))
      if manifest_contains_record "${sync_record}"; then
        synced_ready_count=$((synced_ready_count + 1))
      else
        pending_ready_count=$((pending_ready_count + 1))
      fi
    fi
  done <<< "${workspace_output}"

  if [[ -f "${MANIFEST_FILE}" ]]; then
    while IFS= read -r manifest_record; do
      [[ -z "${manifest_record}" ]] && continue
      case "${manifest_record}" in
        *\|*)
          local signature="${manifest_record#*|}"
          local manifest_epoch
          manifest_epoch="$(epoch_from_signature "${signature}")"
          if [[ -n "${manifest_epoch}" ]]; then
            latest_ready_local_epoch="$(max_number "${latest_ready_local_epoch}" "${manifest_epoch}")"
          fi
          ;;
      esac
    done < "${MANIFEST_FILE}"
  fi

  local sync_state="in_sync"
  if [[ "${pending_ready_count}" -gt 0 ]]; then
    sync_state="behind"
  fi

  local lag_seconds_json="null"
  if [[ -n "${latest_ready_remote_epoch}" && -n "${latest_ready_local_epoch}" ]]; then
    local lag_value
    lag_value="$(awk -v r="${latest_ready_remote_epoch}" -v l="${latest_ready_local_epoch}" 'BEGIN{d=r-l; if (d < 0) d = 0; printf "%.3f", d}')"
    lag_seconds_json="${lag_value}"
  fi

  local latest_ready_remote_epoch_json="null"
  local latest_ready_local_epoch_json="null"
  local latest_ready_signature_json="null"
  local latest_ready_workspace_id_json="null"
  if [[ -n "${latest_ready_remote_epoch}" ]]; then
    latest_ready_remote_epoch_json="${latest_ready_remote_epoch}"
  fi
  if [[ -n "${latest_ready_local_epoch}" ]]; then
    latest_ready_local_epoch_json="${latest_ready_local_epoch}"
  fi
  if [[ -n "${latest_ready_signature}" ]]; then
    latest_ready_signature_json="\"$(json_escape "${latest_ready_signature}")\""
  fi
  if [[ -n "${latest_ready_workspace_id}" ]]; then
    latest_ready_workspace_id_json="\"$(json_escape "${latest_ready_workspace_id}")\""
  fi

  printf '{"ok":true,"sync_state":"%s","pending_ready_count":%d,"ready_total_count":%d,"synced_ready_count":%d,"latest_ready_remote_epoch":%s,"latest_ready_local_epoch":%s,"latest_ready_signature":%s,"latest_ready_workspace_id":%s,"lag_seconds":%s,"require_ready_marker":%s,"ready_marker_filename":"%s","ready_session_prefix":"%s","manifest_file":"%s","remote_host":"%s","remote_dir":"%s","local_dir":"%s","generated_at_epoch":%s}\n' \
    "$(json_escape "${sync_state}")" \
    "${pending_ready_count}" \
    "${ready_total_count}" \
    "${synced_ready_count}" \
    "${latest_ready_remote_epoch_json}" \
    "${latest_ready_local_epoch_json}" \
    "${latest_ready_signature_json}" \
    "${latest_ready_workspace_id_json}" \
    "${lag_seconds_json}" \
    "${REQUIRE_READY_MARKER}" \
    "$(json_escape "${READY_MARKER_FILENAME}")" \
    "$(json_escape "${READY_SESSION_PREFIX}")" \
    "$(json_escape "${MANIFEST_FILE}")" \
    "$(json_escape "${REMOTE_HOST}")" \
    "$(json_escape "${REMOTE_DIR}")" \
    "$(json_escape "${LOCAL_DIR}")" \
    "$(date +%s)"

  return 0
}

if [[ "${STATUS_JSON}" == "true" ]]; then
  sync_status_json
  exit $?
fi

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
if [[ "${REQUIRE_READY_MARKER}" == "true" ]]; then
  if [[ -n "${READY_SESSION_PREFIX}" ]]; then
    log "Ready mode:  enabled for prefixes '${READY_SESSION_PREFIX}' (${READY_MARKER_FILENAME}, min age ${READY_MIN_AGE_SEC}s)"
  else
    log "Ready mode:  enabled for all workspaces (${READY_MARKER_FILENAME}, min age ${READY_MIN_AGE_SEC}s)"
  fi
else
  log "Ready mode:  disabled"
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
