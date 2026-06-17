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
HEARTBEAT_STALE_SECONDS="${UA_WATCHDOG_HEARTBEAT_STALE_SECONDS:-300}"

# --- Cause-aware controls (alert on restart + restart rate-limit/escalation) ---
# Previously a degraded service could flap-restart every 30s cycle indefinitely
# with ZERO operator signal. We now (a) alert on every restart via the existing
# ops-notifications -> email+Telegram fan-out, and (b) cap restarts/hour: once a
# service exceeds the cap we back off auto-restart and escalate instead of
# silently masking a persistent fault.
NOTIFY_ENABLED="${UA_WATCHDOG_NOTIFY_ENABLED:-1}"
MAX_RESTARTS_PER_HOUR="${UA_WATCHDOG_MAX_RESTARTS_PER_HOUR:-6}"
RESTART_WINDOW_SECONDS="${UA_WATCHDOG_RESTART_WINDOW_SECONDS:-3600}"
FLAP_COOLDOWN_SECONDS="${UA_WATCHDOG_FLAP_COOLDOWN_SECONDS:-600}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFIER_SCRIPT="${UA_WATCHDOG_NOTIFIER_SCRIPT:-$SCRIPT_DIR/watchdog_restart_notifier.py}"
NOTIFIER_PYTHON="${UA_WATCHDOG_PYTHON_BIN:-$(dirname "$SCRIPT_DIR")/.venv/bin/python3}"

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

is_heartbeat_fresh() {
  # Check if a heartbeat file exists and was updated within HEARTBEAT_STALE_SECONDS.
  local hb_file="$1"
  if [[ ! -f "$hb_file" ]]; then
    return 1
  fi
  local now file_epoch age
  now="$(date +%s)"
  # Read the timestamp from the file (Unix epoch float, take integer part)
  file_epoch="$(head -1 "$hb_file" 2>/dev/null | cut -d. -f1)"
  if [[ -z "$file_epoch" ]] || ! [[ "$file_epoch" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  age=$((now - file_epoch))
  if [[ "$age" -le "$HEARTBEAT_STALE_SECONDS" ]]; then
    return 0
  fi
  return 1
}

# --- Restart ledger (per-service restart timestamps, for rate-limit/escalation) ---
restart_ledger_for() {
  printf '%s/%s.restarts' "$STATE_DIR" "$(service_key "$1")"
}

flap_alert_marker_for() {
  printf '%s/%s.flapalert' "$STATE_DIR" "$(service_key "$1")"
}

# Prune the ledger to RESTART_WINDOW_SECONDS and echo the count of restarts
# still inside the window (i.e. restarts BEFORE the current one).
restart_count_in_window() {
  local service="$1" file now cutoff line count=0 tmp
  file="$(restart_ledger_for "$service")"
  if [[ ! -f "$file" ]]; then
    printf '0'
    return
  fi
  now="$(date +%s)"
  cutoff=$((now - RESTART_WINDOW_SECONDS))
  tmp="$(mktemp "${STATE_DIR}/.restarts.XXXXXX")"
  while IFS= read -r line; do
    [[ "$line" =~ ^[0-9]+$ ]] || continue
    if [[ "$line" -ge "$cutoff" ]]; then
      printf '%s\n' "$line" >>"$tmp"
      count=$((count + 1))
    fi
  done <"$file"
  mv "$tmp" "$file"
  printf '%s' "$count"
}

last_restart_epoch() {
  local file
  file="$(restart_ledger_for "$1")"
  [[ -f "$file" ]] || { printf '0'; return; }
  tail -1 "$file" 2>/dev/null | grep -oE '^[0-9]+' || printf '0'
}

record_restart() {
  printf '%s\n' "$(date +%s)" >>"$(restart_ledger_for "$1")"
}

# Best-effort dashboard/email/Telegram alert via the Python notifier (which
# bootstraps Infisical to obtain UA_OPS_TOKEN — not present in this oneshot's
# env). Never blocks or fails a restart.
notify_restart() {
  local service="$1" reason="$2" event="$3" escalated="$4" post_state="$5" count="$6"
  [[ "$NOTIFY_ENABLED" == "1" ]] || return 0
  [[ -x "$NOTIFIER_PYTHON" ]] || return 0
  [[ -f "$NOTIFIER_SCRIPT" ]] || return 0
  local esc_flag=()
  [[ "$escalated" == "1" ]] && esc_flag=(--escalated)
  timeout 30 "$NOTIFIER_PYTHON" "$NOTIFIER_SCRIPT" \
    --service "$service" --reason "$reason" --event "$event" \
    --post-state "$post_state" --restart-count "$count" \
    --window-seconds "$RESTART_WINDOW_SECONDS" --max-per-hour "$MAX_RESTARTS_PER_HOUR" \
    "${esc_flag[@]}" 2>&1 | sed 's/^/[ua-watchdog] notifier: /' || true
}

restart_service() {
  local service="$1"
  local reason="$2"

  # Rate-limit: how many times have WE restarted this service in the window?
  local prior_count flapping=0 last now
  prior_count="$(restart_count_in_window "$service")"
  if [[ "$prior_count" -ge "$MAX_RESTARTS_PER_HOUR" ]]; then
    flapping=1
    last="$(last_restart_epoch "$service")"
    now="$(date +%s)"
    if [[ $((now - last)) -lt "$FLAP_COOLDOWN_SECONDS" ]]; then
      # Back off: a service restarted >= cap/hr is not being fixed by more
      # restarts — escalate to a human instead of silently flap-masking.
      log "service=$service action=skip_restart reason=flapping restarts_in_window=$prior_count threshold=$MAX_RESTARTS_PER_HOUR cooldown=${FLAP_COOLDOWN_SECONDS}s"
      local marker marker_age=999999
      marker="$(flap_alert_marker_for "$service")"
      [[ -f "$marker" ]] && marker_age=$((now - $(cat "$marker" 2>/dev/null || echo 0)))
      if [[ "$marker_age" -ge "$FLAP_COOLDOWN_SECONDS" ]]; then
        printf '%s' "$now" >"$marker"
        notify_restart "$service" "$reason" "flapping_backoff" 1 "skipped" "$prior_count"
      fi
      return 1
    fi
    # Cooldown elapsed while flapping: allow one (escalated) restart attempt.
  fi

  log "service=$service action=restart reason=$reason flapping=$flapping restarts_in_window=$prior_count"
  "$SYSTEMCTL_BIN" reset-failed "$service" >/dev/null 2>&1 || true
  if "$SYSTEMCTL_BIN" restart "$service"; then
    if [[ "$POST_RESTART_SETTLE_SECONDS" -gt 0 ]]; then
      sleep "$POST_RESTART_SETTLE_SECONDS"
    fi
    local new_state
    new_state="$("$SYSTEMCTL_BIN" is-active "$service" 2>/dev/null || true)"
    record_restart "$service"
    log "service=$service restart_result=ok post_state=${new_state:-unknown}"
    notify_restart "$service" "$reason" "restart" "$flapping" "${new_state:-unknown}" "$((prior_count + 1))"
    return 0
  fi

  record_restart "$service"
  log "service=$service restart_result=failed"
  notify_restart "$service" "$reason" "restart" 1 "failed" "$((prior_count + 1))"
  return 1
}

check_service() {
  local service="$1"
  local health_url="${2:-}"
  local heartbeat_file="${3:-}"

  local active_state
  active_state="$("$SYSTEMCTL_BIN" is-active "$service" 2>/dev/null || true)"
  if [[ "$active_state" != "active" ]]; then
    restart_service "$service" "inactive:$active_state" || true
    reset_fail_count "$service"
    return
  fi

  # --- Heartbeat file check (preferred, event-loop independent) ---
  if [[ -n "$heartbeat_file" ]]; then
    if is_heartbeat_fresh "$heartbeat_file"; then
      local previous
      previous="$(read_fail_count "$service")"
      reset_fail_count "$service"
      if [[ "$previous" -gt 0 ]]; then
        log "service=$service health=recovered method=heartbeat previous_failures=$previous"
      fi
      return
    fi
    # Heartbeat stale — count as failure
    local failures
    failures="$(read_fail_count "$service")"
    failures=$((failures + 1))
    write_fail_count "$service" "$failures"
    log "service=$service health=failed method=heartbeat_stale file=$heartbeat_file consecutive_failures=$failures threshold=$HEALTH_FAIL_THRESHOLD"
    if [[ "$failures" -ge "$HEALTH_FAIL_THRESHOLD" ]]; then
      restart_service "$service" "heartbeat_stale" || true
      reset_fail_count "$service"
    fi
    return
  fi

  # --- HTTP health check (fallback for services without heartbeat file) ---
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
      log "service=$service health=recovered method=http status_code=$code previous_failures=$previous"
    else
      log "service=$service health=ok method=http status_code=$code"
    fi
    return
  fi

  local failures
  failures="$(read_fail_count "$service")"
  failures=$((failures + 1))
  write_fail_count "$service" "$failures"
  log "service=$service health=failed method=http status_code=$code consecutive_failures=$failures threshold=$HEALTH_FAIL_THRESHOLD"

  if [[ "$failures" -lt "$HEALTH_FAIL_THRESHOLD" ]]; then
    return
  fi

  restart_service "$service" "healthcheck:${code}" || true
  reset_fail_count "$service"
}

# Service specs format: service_name|http_health_url|heartbeat_file
# If heartbeat_file is set, it takes priority over http_health_url.
DEFAULT_HEARTBEAT_DIR="/var/lib/universal-agent/heartbeat"
# mission-control-sweeper has no HTTP endpoint and no heartbeat file, so it is
# is-active-monitored only: a healthy active process is a no-op, but a dead /
# start-limit-exhausted unit gets reset-failed + restarted (the backstop a
# now-standalone process needs; Restart=always covers single crashes).
DEFAULT_SERVICE_SPECS=$'universal-agent-gateway|http://127.0.0.1:8002/api/v1/health|/var/lib/universal-agent/heartbeat/gateway.heartbeat\nuniversal-agent-api|http://127.0.0.1:8001/api/health|\nuniversal-agent-webui|http://127.0.0.1:3000/|\nuniversal-agent-telegram||/var/lib/universal-agent/heartbeat/telegram.heartbeat\nuniversal-agent-mission-control-sweeper||\ncsi-ingester|http://127.0.0.1:8091/healthz|'
SERVICE_SPECS="${UA_WATCHDOG_SERVICE_SPECS:-$DEFAULT_SERVICE_SPECS}"

while IFS= read -r spec; do
  [[ -z "$spec" ]] && continue
  IFS='|' read -r service health_url heartbeat_file <<<"$spec"
  service="${service:-}"
  health_url="${health_url:-}"
  heartbeat_file="${heartbeat_file:-}"
  [[ -z "$service" ]] && continue
  check_service "$service" "$health_url" "$heartbeat_file"
done <<<"$SERVICE_SPECS"

log "watchdog cycle complete"
