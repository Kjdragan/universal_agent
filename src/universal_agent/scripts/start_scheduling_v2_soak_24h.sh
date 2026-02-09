#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

TS_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${ROOT_DIR}/OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews"
mkdir -p "$RUN_DIR"

PORT="${UA_GATEWAY_PORT:-8002}"
BASE_URL="${UA_SOAK_BASE_URL:-http://127.0.0.1:${PORT}}"
DURATION_SECONDS="${UA_SOAK_DURATION_SECONDS:-86400}"
INTERVAL_SECONDS="${UA_SOAK_INTERVAL_SECONDS:-30}"
TIMEOUT_SECONDS="${UA_SOAK_TIMEOUT_SECONDS:-8}"

REPORT_JSON="${RUN_DIR}/scheduling_v2_soak_24h_${TS_UTC}.json"
STATUS_JSON="${RUN_DIR}/scheduling_v2_soak_24h_${TS_UTC}.status.json"
SOAK_LOG="${RUN_DIR}/scheduling_v2_soak_24h_${TS_UTC}.log"
SOAK_PID_FILE="${RUN_DIR}/scheduling_v2_soak_24h_${TS_UTC}.pid"

GATEWAY_LOG="${RUN_DIR}/scheduling_v2_gateway_${TS_UTC}.log"
GATEWAY_PID_FILE="${RUN_DIR}/scheduling_v2_gateway_${TS_UTC}.pid"
GATEWAY_STARTED_BY_SCRIPT="false"

check_health() {
  curl -fsS -m 2 "${BASE_URL}/api/v1/health" >/dev/null 2>&1
}

if ! check_health; then
  UA_GATEWAY_PORT="$PORT" \
  UA_ENABLE_CRON="${UA_ENABLE_CRON:-1}" \
  UA_CRON_MOCK_RESPONSE="${UA_CRON_MOCK_RESPONSE:-1}" \
  UA_SCHED_EVENT_PROJECTION_ENABLED="${UA_SCHED_EVENT_PROJECTION_ENABLED:-1}" \
  UA_SCHED_PUSH_ENABLED="${UA_SCHED_PUSH_ENABLED:-1}" \
    nohup uv run python -m universal_agent.gateway_server >"$GATEWAY_LOG" 2>&1 &
  GATEWAY_PID=$!
  echo "$GATEWAY_PID" >"$GATEWAY_PID_FILE"
  GATEWAY_STARTED_BY_SCRIPT="true"
fi

for _ in $(seq 1 90); do
  if check_health; then
    break
  fi
  sleep 1
done

if ! check_health; then
  echo "Gateway is not healthy at ${BASE_URL}; aborting soak launch." >&2
  exit 1
fi

nohup uv run python src/universal_agent/scripts/scheduling_v2_soak.py \
  --base-url "$BASE_URL" \
  --duration-seconds "$DURATION_SECONDS" \
  --interval-seconds "$INTERVAL_SECONDS" \
  --timeout-seconds "$TIMEOUT_SECONDS" \
  --out-json "$REPORT_JSON" \
  --status-json "$STATUS_JSON" \
  >"$SOAK_LOG" 2>&1 &
SOAK_PID=$!
echo "$SOAK_PID" >"$SOAK_PID_FILE"

cat <<EOF
{
  "started_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "base_url": "${BASE_URL}",
  "soak_pid": ${SOAK_PID},
  "soak_pid_file": "${SOAK_PID_FILE}",
  "soak_log": "${SOAK_LOG}",
  "status_json": "${STATUS_JSON}",
  "report_json": "${REPORT_JSON}",
  "gateway_started_by_script": ${GATEWAY_STARTED_BY_SCRIPT},
  "gateway_pid_file": "${GATEWAY_PID_FILE}",
  "gateway_log": "${GATEWAY_LOG}"
}
EOF
