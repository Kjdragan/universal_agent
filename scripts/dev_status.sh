#!/usr/bin/env bash
# ==============================================================================
# scripts/dev_status.sh
# ------------------------------------------------------------------------------
# Read-only snapshot of local dev + VPS coordination state. Safe to run any
# time. Answers:
#   - Is the local stack running? Which processes, which ports, which logs?
#   - Is the VPS currently paused by a dev_up.sh hot-swap?
#   - What state are the VPS conflict services in?
#
# Never modifies anything. Never writes to disk.
# ==============================================================================
set -Eeuo pipefail

# Keep in sync with dev_up.sh / dev_down.sh.
VPS_CONFLICT_SERVICES=(
  "universal-agent-api.service"
  "universal-agent-gateway.service"
  "universal-agent-webui.service"
  "universal-agent-telegram.service"
  "ua-discord-cc-bot.service"
  "universal-agent-service-watchdog.service"
)

VPS_CONFLICT_TIMERS=(
  "universal-agent-service-watchdog.timer"
  "universal-agent-youtube-playlist-poller.timer"
)

APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PID_FILE="${UA_LOCAL_PID_FILE:-/tmp/ua-local-dev.pids}"
LOG_DIR="${UA_LOCAL_LOG_DIR:-/tmp/ua-local-logs}"
LOCAL_DATA_DIR="${UA_LOCAL_DATA_DIR:-$HOME/lrepos/universal_agent_local_data}"

UA_API_PORT="${UA_API_PORT:-8001}"
UA_GATEWAY_PORT="${UA_GATEWAY_PORT:-8002}"
UA_WEBUI_PORT="${UA_WEBUI_PORT:-3000}"

VPS_SSH_HOST="${UA_VPS_SSH_HOST:-root@uaonvps}"
VPS_SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
VPS_PAUSE_STAMP_PATH="${UA_VPS_PAUSE_STAMP_PATH:-/etc/universal-agent/dev_pause.stamp}"
VPS_CHECK_SKIP="${UA_VPS_STATUS_SKIP:-0}"

if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'
  C_CYA=$'\033[36m'; C_BLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
  C_RED=""; C_YEL=""; C_GRN=""; C_CYA=""; C_BLD=""; C_DIM=""; C_RST=""
fi

heading() { printf '\n%s%s=== %s ===%s\n' "$C_BLD" "$C_CYA" "$*" "$C_RST"; }
ok()      { printf '  %sOK%s   %s\n' "$C_GRN" "$C_RST" "$*"; }
bad()     { printf '  %sFAIL%s %s\n' "$C_RED" "$C_RST" "$*"; }
note()    { printf '  %s-%s    %s\n' "$C_DIM" "$C_RST" "$*"; }

# ------------------------------------------------------------------------------
# Local
# ------------------------------------------------------------------------------
heading "Local stack"

if [[ -f "$PID_FILE" ]]; then
  note "PID file: $PID_FILE"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    name="${line%%:*}"
    pid="${line##*:}"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      ok "$name pid=$pid alive"
    else
      bad "$name pid=$pid NOT alive"
    fi
  done < "$PID_FILE"
else
  note "No PID file at $PID_FILE (local stack is presumed stopped)"
fi

check_port() {
  local label="$1" port="$2" path="${3:-/}"
  local url="http://127.0.0.1:${port}${path}"
  if curl -fsS -o /dev/null "$url" 2>/dev/null; then
    ok "$label  $url"
  else
    bad "$label  $url unreachable"
  fi
}

check_port "web-ui "  "$UA_WEBUI_PORT"  "/"
check_port "api     " "$UA_API_PORT"    "/api/health"
check_port "gateway " "$UA_GATEWAY_PORT" "/api/dashboard/gateway/health"

if [[ -d "$LOG_DIR" ]]; then
  note "Logs: $LOG_DIR"
  for f in "$LOG_DIR"/*.log; do
    [[ -f "$f" ]] || continue
    size=$(wc -c <"$f" 2>/dev/null || echo 0)
    note "  $(basename "$f")  (${size} bytes)"
  done
else
  note "No log dir at $LOG_DIR"
fi

if [[ -d "$LOCAL_DATA_DIR" ]]; then
  note "Data dir: $LOCAL_DATA_DIR"
else
  note "Data dir does not exist yet: $LOCAL_DATA_DIR"
fi

# ------------------------------------------------------------------------------
# VPS
# ------------------------------------------------------------------------------
heading "VPS coordination ($VPS_SSH_HOST)"

if [[ "$VPS_CHECK_SKIP" == "1" ]]; then
  note "UA_VPS_STATUS_SKIP=1 — skipping VPS check"
  exit 0
fi

# Build one read-only remote command that returns pause stamp + unit states.
remote_cmd=$(cat <<REMOTE
set -e
echo "---PAUSE_STAMP---"
if [ -f '$VPS_PAUSE_STAMP_PATH' ]; then
  cat '$VPS_PAUSE_STAMP_PATH'
else
  echo "ABSENT"
fi
echo "---UNITS---"
for u in $(printf "'%s' " "${VPS_CONFLICT_SERVICES[@]}" "${VPS_CONFLICT_TIMERS[@]}"); do
  state="\$(systemctl is-active \"\$u\" 2>/dev/null || true)"
  enabled="\$(systemctl is-enabled \"\$u\" 2>/dev/null || true)"
  printf '%s\t%s\t%s\n' "\$u" "\$state" "\$enabled"
done
REMOTE
)

if ! vps_out=$(ssh -i "$VPS_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 \
                   "$VPS_SSH_HOST" "bash -s" <<<"$remote_cmd" 2>/dev/null); then
  bad "ssh $VPS_SSH_HOST failed. Try: ssh -i $VPS_SSH_KEY $VPS_SSH_HOST 'echo ok'"
  exit 0
fi

pause_section=$(awk '/^---PAUSE_STAMP---$/{flag=1; next} /^---UNITS---$/{flag=0} flag' <<<"$vps_out")
units_section=$(awk '/^---UNITS---$/{flag=1; next} flag' <<<"$vps_out")

if [[ "$pause_section" == "ABSENT" ]]; then
  ok "pause stamp: absent (VPS is in NORMAL state)"
else
  printf '  %sPAUSE STAMP PRESENT%s\n' "$C_YEL" "$C_RST"
  while IFS= read -r stamp_line; do
    [[ -z "$stamp_line" ]] && continue
    note "  $stamp_line"
  done <<<"$pause_section"
fi

printf '\n'
while IFS=$'\t' read -r unit state enabled; do
  [[ -z "$unit" ]] && continue
  case "$state" in
    active)    ok   "$unit  (active, $enabled)" ;;
    inactive|failed|"")  bad  "$unit  (${state:-unknown}, $enabled)" ;;
    *)         note "$unit  ($state, $enabled)" ;;
  esac
done <<<"$units_section"

printf '\n'
