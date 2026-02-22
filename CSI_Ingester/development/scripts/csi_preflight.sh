#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./csi_dev_env.sh
source "$SCRIPT_DIR/csi_dev_env.sh" >/dev/null

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
fi

fail() {
  echo "PRECHECK FAIL: $1" >&2
  exit 1
}

warn() {
  echo "PRECHECK WARN: $1" >&2
}

check_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

check_cmd python3
check_cmd uv

CONFIG_PATH="${CSI_CONFIG_PATH:-$PWD/config/config.yaml}"
if [[ ! -f "$CONFIG_PATH" ]]; then
  fail "config file not found: $CONFIG_PATH"
fi

python3 - <<'PY' "$CONFIG_PATH" || fail "config YAML parse failed"
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
if not isinstance(payload, dict):
    raise SystemExit(2)
print(f"CONFIG_OK={path}")
PY

if [[ -z "${CSI_UA_ENDPOINT:-}" ]]; then
  if [[ "$STRICT" -eq 1 ]]; then
    fail "CSI_UA_ENDPOINT is required in --strict mode"
  fi
  warn "CSI_UA_ENDPOINT not set (delivery disabled)"
fi

if [[ -z "${CSI_UA_SHARED_SECRET:-}" ]]; then
  if [[ "$STRICT" -eq 1 ]]; then
    fail "CSI_UA_SHARED_SECRET is required in --strict mode"
  fi
  warn "CSI_UA_SHARED_SECRET not set (delivery auth disabled)"
fi

if [[ -z "${YOUTUBE_API_KEY:-}" ]]; then
  warn "YOUTUBE_API_KEY not set (playlist adapter will not poll)"
fi

echo "PRECHECK OK"

