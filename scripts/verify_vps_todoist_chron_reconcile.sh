#!/usr/bin/env bash
set -euo pipefail

# Secure one-command verifier for Todoist<->Chron reconciliation on VPS.
#
# What it does:
# 1) Resolves UA_OPS_TOKEN from the running gateway service process environment.
# 2) Runs authenticated dry-run and live reconciliation calls.
# 3) Prints sanitized result and metrics blocks (never prints token).
#
# Usage:
#   scripts/verify_vps_todoist_chron_reconcile.sh
#
# Optional env:
#   UA_VPS_HOST=root@100.106.113.93
#   UA_VPS_SSH_KEY=~/.ssh/id_ed25519
#   UA_SSH_AUTH_MODE=keys|tailscale_ssh
#   UA_GATEWAY_INTERNAL_URL=http://127.0.0.1:8002

VPS_HOST="${UA_VPS_HOST:-root@100.106.113.93}"
SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_AUTH_MODE="${UA_SSH_AUTH_MODE:-keys}"
GATEWAY_INTERNAL_URL="${UA_GATEWAY_INTERNAL_URL:-http://127.0.0.1:8002}"

ssh_cmd=(ssh)
case "$(printf '%s' "${SSH_AUTH_MODE}" | tr '[:upper:]' '[:lower:]')" in
  keys)
    if [[ -n "${SSH_KEY}" ]]; then
      ssh_cmd+=(-i "${SSH_KEY}")
    fi
    ;;
  tailscale_ssh)
    ;;
  *)
    echo "ERROR: UA_SSH_AUTH_MODE must be keys or tailscale_ssh (got: ${SSH_AUTH_MODE})" >&2
    exit 2
    ;;
esac

"${ssh_cmd[@]}" "${VPS_HOST}" "UA_GATEWAY_INTERNAL_URL='${GATEWAY_INTERNAL_URL}' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

SERVICE="universal-agent-gateway"
BASE_URL="${UA_GATEWAY_INTERNAL_URL:-http://127.0.0.1:8002}"

echo "NOW_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "service=${SERVICE}"
echo "base_url=${BASE_URL}"

main_pid="$(systemctl show "${SERVICE}" -p MainPID --value 2>/dev/null || true)"
if [[ -z "${main_pid}" || "${main_pid}" == "0" ]]; then
  echo "ERROR: ${SERVICE} is not running (MainPID=${main_pid:-empty})." >&2
  exit 3
fi
echo "main_pid=${main_pid}"

environ_path="/proc/${main_pid}/environ"
if [[ ! -r "${environ_path}" ]]; then
  echo "ERROR: cannot read ${environ_path}" >&2
  exit 4
fi

ops_token="$(tr '\0' '\n' < "${environ_path}" | sed -n 's/^UA_OPS_TOKEN=//p' | tail -n1)"
token_len=${#ops_token}
echo "ops_token_len=${token_len}"
if (( token_len == 0 )); then
  echo "ERROR: UA_OPS_TOKEN is not present in running gateway process environment." >&2
  exit 5
fi

dry_json="$(curl -sS -X POST \
  -H "authorization: Bearer ${ops_token}" \
  -H "x-ua-ops-token: ${ops_token}" \
  "${BASE_URL}/api/v1/ops/reconcile/todoist-chron?dry_run=true")"

live_json="$(curl -sS -X POST \
  -H "authorization: Bearer ${ops_token}" \
  -H "x-ua-ops-token: ${ops_token}" \
  "${BASE_URL}/api/v1/ops/reconcile/todoist-chron")"

metrics_json="$(curl -sS \
  -H "authorization: Bearer ${ops_token}" \
  -H "x-ua-ops-token: ${ops_token}" \
  "${BASE_URL}/api/v1/ops/metrics/scheduling-runtime")"

summary_json="$(curl -sS "${BASE_URL}/api/v1/dashboard/summary")"
unset ops_token

DRY_JSON="${dry_json}" \
LIVE_JSON="${live_json}" \
METRICS_JSON="${metrics_json}" \
SUMMARY_JSON="${summary_json}" \
python3 - <<'PY'
import json
import os

def parse_env(name):
    raw = os.environ.get(name, "")
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw, "parse_error": True}

dry = parse_env("DRY_JSON")
live = parse_env("LIVE_JSON")
metrics = parse_env("METRICS_JSON")
summary = parse_env("SUMMARY_JSON")

print("---RECON_DRY_RUN---")
print(json.dumps(dry.get("reconciliation", dry), ensure_ascii=True, indent=2))
print("---RECON_LIVE---")
print(json.dumps(live.get("reconciliation", live), ensure_ascii=True, indent=2))
print("---RECON_METRICS---")
print(
    json.dumps(
        ((metrics.get("metrics") or {}).get("todoist_chron_reconciliation") or {}),
        ensure_ascii=True,
        indent=2,
    )
)
print("---DASHBOARD_SUMMARY_BLOCK---")
print(json.dumps(summary.get("todoist_chron_reconciliation", {}), ensure_ascii=True, indent=2))
PY
REMOTE_SCRIPT
