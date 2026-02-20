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

PY_BIN="${PY_BIN:-${REPO_ROOT}/.venv/bin/python}"
if [[ ! -x "${PY_BIN}" ]]; then
  PY_BIN="python3"
fi

exec "${PY_BIN}" -m universal_agent.vp.worker_main --vp-id "${VP_ID}" "$@"
