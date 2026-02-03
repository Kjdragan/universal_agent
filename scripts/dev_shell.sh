#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${REPO_ROOT}/.venv"

if [[ ! -d "${VENV_PATH}" ]]; then
  echo "Missing .venv at ${VENV_PATH}. Run: uv sync --frozen --no-dev" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_PATH}/bin/activate"

echo "✅ Activated venv: ${VENV_PATH}"
echo "Use: python -V, pytest -q"
exec "${SHELL:-/bin/bash}"
