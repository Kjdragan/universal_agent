#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYNC_SCRIPT="${REPO_ROOT}/scripts/sync_remote_workspaces.sh"

REMOTE_HOST="${UA_REMOTE_SSH_HOST:-root@100.106.113.93}"
REMOTE_DIR="${UA_REMOTE_WORKSPACES_DIR:-/opt/universal_agent/AGENT_RUN_WORKSPACES}"
LOCAL_DIR="${UA_LOCAL_MIRROR_DIR:-${REPO_ROOT}/AGENT_RUN_WORKSPACES/remote_vps_workspaces}"
REMOTE_ARTIFACTS_DIR="${UA_REMOTE_ARTIFACTS_DIR:-/opt/universal_agent/artifacts}"
LOCAL_ARTIFACTS_DIR="${UA_LOCAL_ARTIFACTS_MIRROR_DIR:-${REPO_ROOT}/artifacts/remote_vps_artifacts}"
MANIFEST_FILE="${UA_REMOTE_SYNC_MANIFEST_FILE:-${REPO_ROOT}/AGENT_RUN_WORKSPACES/remote_vps_sync_state/synced_workspaces.txt}"
SSH_KEY="${UA_REMOTE_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
SSH_PORT="${UA_REMOTE_SSH_PORT:-22}"
INCLUDE_ARTIFACTS_SYNC="${UA_REMOTE_SYNC_INCLUDE_ARTIFACTS:-true}"
REQUIRE_READY_MARKER="${UA_REMOTE_SYNC_REQUIRE_READY_MARKER:-true}"
READY_MARKER_FILENAME="${UA_REMOTE_SYNC_READY_MARKER_FILENAME:-sync_ready.json}"
READY_MIN_AGE_SEC="${UA_REMOTE_SYNC_READY_MIN_AGE_SECONDS:-45}"
READY_SESSION_PREFIX="${UA_REMOTE_SYNC_READY_SESSION_PREFIX:-session_,tg_}"

SESSION_ID=""
if [[ $# -gt 0 && "${1:-}" != --* ]]; then
  SESSION_ID="$1"
  shift
fi

args=(
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

if [[ "${INCLUDE_ARTIFACTS_SYNC}" != "true" ]]; then
  args+=(--no-artifacts)
fi

case "$(printf '%s' "${REQUIRE_READY_MARKER}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    args+=(--require-ready-marker)
    ;;
  *)
    args+=(--ignore-ready-marker)
    ;;
esac
args+=(--ready-marker-name "${READY_MARKER_FILENAME}")
args+=(--ready-min-age-seconds "${READY_MIN_AGE_SEC}")
if [[ -n "${READY_SESSION_PREFIX}" ]]; then
  args+=(--ready-session-prefix "${READY_SESSION_PREFIX}")
fi

if [[ -n "${SESSION_ID}" ]]; then
  args+=(--session-id "${SESSION_ID}")
fi

# Optional passthrough flags, e.g. --no-skip-synced
args+=("$@")

exec "${SYNC_SCRIPT}" "${args[@]}"
