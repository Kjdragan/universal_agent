#!/usr/bin/env bash
set -Eeuo pipefail

log() {
  printf '[ua-watchdog] %s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

SYSTEMCTL_BIN="${UA_WATCHDOG_SYSTEMCTL_BIN:-systemctl}"
CURL_BIN="${UA_WATCHDOG_CURL_BIN:-curl}"
STATE_DIR="${UA_WATCHDOG_STATE_DIR:-/var/lib/universal-agent/watchdog}"
HEALTH_FAIL_THRESHOLD="${UA_WATCHDOG_HEALTH_FAIL_THRESHOLD:-3}"
HTTP_TIMEOUT_SECONDS="${UA_WATCHDOG_HTTP_TIMEOUT_SECONDS:-8}"
HTTP_OK_MAX_STATUS="${UA_WATCHDOG_HTTP_OK_MAX_STATUS:-499}"
POST_RESTART_SETTLE_SECONDS="${UA_WATCHDOG_POST_RESTART_SETTLE_SECONDS:-2}"

if ! [[ "$HEALTH_FAIL_THRESHOLD" =~ ^[0-9]+$ ]] || [[ "$HEALTH_FAIL_THRESHOLD" -lt 1 ]]; then
  log "invalid UA_WATCHDOG_HEALTH_FAIL_THRESHOLD=$HEALTH_FAIL_THRESHOLD (must be >=1)"
  exit 2
fi

if ! [[ "$HTTP_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [[ "$HTTP_TIMEOUT_SECONDS" -lt 1 ]]; then
  log "invalid UA_WATCHDOG_HTTP_TIMEOUT_SECONDS=$HTTP_TIMEOUT_SECONDS (must be >=1)"
  exit 2
fi

if ! [[ "$HTTP_OK_MAX_STATUS" =~ ^[0-9]+$ ]] || [[ "$HTTP_OK_MAX_STATUS" -lt 100 ]]; then
  log "invalid UA_WATCHDOG_HTTP_OK_MAX_STATUS=$HTTP_OK_MAX_STATUS (must be >=100)"
  exit 2
fi

if ! [[ "$POST_RESTART_SETTLE_SECONDS" =~ ^[0-9]+$ ]]; then
  log "invalid UA_WATCHDOG_POST_RESTART_SETTLE_SECONDS=$POST_RESTART_SETTLE_SECONDS"
  exit 2
fi

mkdir -p "$STATE_DIR"

service_key() {
  local raw="$1"
  printf '%s' "$raw" | tr '/:@. ' '_____'
}

fail_file_for() {
  local service="$1"
  printf '%s/%s.failcount' "$STATE_DIR" "$(service_key "$service")"
}

read_fail_count() {
  local service="$1"
  local file
  file="$(fail_file_for "$service")"
  if [[ ! -f "$file" ]]; then
    printf '0'
    return
  fi
  local value
  value="$(cat "$file" 2>/dev/null || true)"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s' "$value"
  else
    printf '0'
  fi
}

write_fail_count() {
  local service="$1"
  local value="$2"
  printf '%s' "$value" >"$(fail_file_for "$service")"
}

reset_fail_count() {
  local service="$1"
  write_fail_count "$service" 0
}

http_status_code() {
  local url="$1"
  local code
  code="$("$CURL_BIN" -sS -o /dev/null -w '%{http_code}' --max-time "$HTTP_TIMEOUT_SECONDS" "$url" 2>/dev/null || printf '000')"
  if [[ ! "$code" =~ ^[0-9]{3}$ ]]; then
    code="000"
  fi
  printf '%s' "$code"
}

is_http_healthy() {
  local code="$1"
  if [[ "$code" -eq 0 ]]; then
    return 1
  fi
  if [[ "$code" -ge 100 && "$code" -le "$HTTP_OK_MAX_STATUS" ]]; then
    return 0
  fi
  return 1
}

restart_service() {
  local service="$1"
  local reason="$2"

  log "service=$service action=restart reason=$reason"
  "$SYSTEMCTL_BIN" reset-failed "$service" >/dev/null 2>&1 || true
  if "$SYSTEMCTL_BIN" restart "$service"; then
    if [[ "$POST_RESTART_SETTLE_SECONDS" -gt 0 ]]; then
      sleep "$POST_RESTART_SETTLE_SECONDS"
    fi
    local new_state
    new_state="$("$SYSTEMCTL_BIN" is-active "$service" 2>/dev/null || true)"
    log "service=$service restart_result=ok post_state=${new_state:-unknown}"
    return 0
  fi

  log "service=$service restart_result=failed"
  return 1
}

check_service() {
  local service="$1"
  local health_url="${2:-}"

  local active_state
  active_state="$("$SYSTEMCTL_BIN" is-active "$service" 2>/dev/null || true)"
  if [[ "$active_state" != "active" ]]; then
    restart_service "$service" "inactive:$active_state" || true
    reset_fail_count "$service"
    return
  fi

  if [[ -z "$health_url" ]]; then
    reset_fail_count "$service"
    log "service=$service state=active health=not_configured"
    return
  fi

  local code
  code="$(http_status_code "$health_url")"
  if is_http_healthy "$code"; then
    local previous
    previous="$(read_fail_count "$service")"
    reset_fail_count "$service"
    if [[ "$previous" -gt 0 ]]; then
      log "service=$service health=recovered status_code=$code previous_failures=$previous"
    else
      log "service=$service health=ok status_code=$code"
    fi
    return
  fi

  local failures
  failures="$(read_fail_count "$service")"
  failures=$((failures + 1))
  write_fail_count "$service" "$failures"
  log "service=$service health=failed status_code=$code consecutive_failures=$failures threshold=$HEALTH_FAIL_THRESHOLD"

  if [[ "$failures" -lt "$HEALTH_FAIL_THRESHOLD" ]]; then
    return
  fi

  restart_service "$service" "healthcheck:${code}" || true
  reset_fail_count "$service"
}

DEFAULT_SERVICE_SPECS=$'universal-agent-gateway|http://127.0.0.1:8002/api/v1/health\nuniversal-agent-api|http://127.0.0.1:8001/api/health\nuniversal-agent-webui|http://127.0.0.1:3000/\nuniversal-agent-telegram|\ncsi-ingester|http://127.0.0.1:8091/healthz'
SERVICE_SPECS="${UA_WATCHDOG_SERVICE_SPECS:-$DEFAULT_SERVICE_SPECS}"

while IFS= read -r spec; do
  [[ -z "$spec" ]] && continue
  IFS='|' read -r service health_url <<<"$spec"
  service="${service:-}"
  health_url="${health_url:-}"
  [[ -z "$service" ]] && continue
  check_service "$service" "$health_url"
done <<<"$SERVICE_SPECS"

log "watchdog cycle complete"
