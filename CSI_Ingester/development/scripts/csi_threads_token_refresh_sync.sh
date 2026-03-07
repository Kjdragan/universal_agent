#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/universal_agent}"
CSI_DIR="${CSI_DIR:-${ROOT_DIR}/CSI_Ingester/development}"
ENV_FILE="${CSI_ENV_FILE:-${CSI_DIR}/deployment/systemd/csi-ingester.env}"
PYTHON_BIN="${CSI_PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python3}"
CONFIG_PATH="${CSI_THREADS_PROBE_CONFIG_PATH:-${CSI_DIR}/config/config.yaml}"

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "THREADS_REFRESH_SYNC_FAIL missing_python_bin=${PYTHON_BIN}"
  exit 2
fi

if [ -f "${ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
fi

for key in INFISICAL_CLIENT_ID INFISICAL_CLIENT_SECRET INFISICAL_PROJECT_ID; do
  if [ -z "${!key:-}" ]; then
    echo "THREADS_REFRESH_SYNC_FAIL missing_${key}"
    exit 2
  fi
done

thread_exports="$(
  PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}" "${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import shlex
import sys

from universal_agent.infisical_loader import _fetch_infisical_secrets

required = ("THREADS_APP_ID", "THREADS_APP_SECRET", "THREADS_ACCESS_TOKEN")
optional = ("THREADS_USER_ID", "THREADS_TOKEN_EXPIRES_AT")

secrets = _fetch_infisical_secrets()
missing = [key for key in required if not str(secrets.get(key) or "").strip()]
if missing:
    print("THREADS_REFRESH_SYNC_FAIL missing_infisical_threads_keys=" + ",".join(missing))
    sys.exit(2)

for key in required + optional:
    value = str(secrets.get(key) or "")
    print(f"export {key}={shlex.quote(value)}")
PY
)"
eval "${thread_exports}"

tmp_json="$(mktemp /tmp/csi-threads-secrets.XXXXXX.json)"
cleanup() {
  rm -f "${tmp_json}"
}
trap cleanup EXIT

"${PYTHON_BIN}" "${CSI_DIR}/scripts/csi_threads_auth_bootstrap.py" \
  --mode refresh \
  --app-id "${THREADS_APP_ID}" \
  --app-secret "${THREADS_APP_SECRET}" \
  --user-id "${THREADS_USER_ID:-}" \
  --access-token "${THREADS_ACCESS_TOKEN}" \
  --token-expires-at "${THREADS_TOKEN_EXPIRES_AT:-}" \
  --timeout-seconds "${CSI_THREADS_REFRESH_TIMEOUT_SECONDS:-20}" \
  --refresh-buffer-seconds "${CSI_THREADS_REFRESH_BUFFER_SECONDS:-21600}" \
  --skip-env-write \
  --infisical-json-file "${tmp_json}"

"${PYTHON_BIN}" "${CSI_DIR}/scripts/csi_threads_infisical_sync.py" \
  --updates-file "${tmp_json}"

updated_exports="$(
  "${PYTHON_BIN}" - <<'PY' "${tmp_json}"
from __future__ import annotations

import json
import shlex
import sys

payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
for key in ("THREADS_APP_ID", "THREADS_APP_SECRET", "THREADS_USER_ID", "THREADS_ACCESS_TOKEN", "THREADS_TOKEN_EXPIRES_AT"):
    print(f"export {key}={shlex.quote(str(payload.get(key) or ''))}")
print(f"THREADS_REFRESHED_EXPIRES_AT={payload.get('THREADS_TOKEN_EXPIRES_AT') or ''}")
PY
)"
eval "${updated_exports}"
echo "THREADS_REFRESHED_EXPIRES_AT=${THREADS_REFRESHED_EXPIRES_AT}"

if [ "${CSI_THREADS_REFRESH_RUN_PROBE:-1}" = "1" ]; then
  set +e
  "${PYTHON_BIN}" "${CSI_DIR}/scripts/csi_threads_probe.py" \
    --config-path "${CONFIG_PATH}" \
    --source "${CSI_THREADS_PROBE_SOURCE:-owned}" \
    --limit "${CSI_THREADS_PROBE_LIMIT:-3}"
  probe_exit="$?"
  set -e
  if [ "${probe_exit}" -ne 0 ]; then
    if [ "${CSI_THREADS_REFRESH_REQUIRE_PROBE_OK:-1}" = "1" ]; then
      echo "THREADS_REFRESH_SYNC_FAIL probe_exit=${probe_exit}"
      exit "${probe_exit}"
    fi
    echo "THREADS_REFRESH_SYNC_WARN probe_exit=${probe_exit}"
  fi
fi

echo "THREADS_REFRESH_SYNC_OK=1"
