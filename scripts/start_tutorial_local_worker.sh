#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$REPO_ROOT/.uv-cache}"
mkdir -p "$UV_CACHE_DIR"

if [ -z "${UA_TUTORIAL_BOOTSTRAP_GATEWAY_URL:-}" ] && [ -n "${UA_GATEWAY_URL:-}" ]; then
  export UA_TUTORIAL_BOOTSTRAP_GATEWAY_URL="$UA_GATEWAY_URL"
fi

exec uv run python scripts/tutorial_local_bootstrap_worker.py
