#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./csi_dev_env.sh
source "$SCRIPT_DIR/csi_dev_env.sh" >/dev/null

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <command> [args...]"
  exit 2
fi

exec "$@"

