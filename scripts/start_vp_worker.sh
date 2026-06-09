#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <vp_id> [--worker-id <id>] [--workspace-base <path>] [--db-path <path>]" >&2
  exit 1
fi

VP_ID="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
export UV_CACHE_DIR="${HOME}/.cache/uv"
export PYTHONHASHSEED="${PYTHONHASHSEED:-1}"
mkdir -p "${UV_CACHE_DIR}" 2>/dev/null || true

# `exec` so the worker process becomes the systemd MainPID. This matters for the
# graceful-drain path: the unit uses KillMode=mixed, which SIGTERMs only the
# MainPID. If this shell stayed the MainPID the SIGTERM would never reach the
# worker's drain handler (installed in worker_main) and systemd would orphan the
# worker, then SIGKILL it mid-mission. With exec, SIGTERM reaches the worker so
# it finishes the in-flight mission before exiting.
if command -v uv >/dev/null 2>&1; then
  exec uv run python -m universal_agent.vp.worker_main --vp-id "${VP_ID}" "$@"
fi

PY_BIN="${PY_BIN:-${REPO_ROOT}/.venv/bin/python}"
if [[ ! -x "${PY_BIN}" ]]; then
  PY_BIN="python3"
fi

exec "${PY_BIN}" -m universal_agent.vp.worker_main --vp-id "${VP_ID}" "$@"
