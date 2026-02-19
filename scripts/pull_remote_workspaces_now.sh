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

if [[ -n "${SESSION_ID}" ]]; then
  args+=(--session-id "${SESSION_ID}")
fi

# Optional passthrough flags, e.g. --no-skip-synced
args+=("$@")

exec "${SYNC_SCRIPT}" "${args[@]}"
