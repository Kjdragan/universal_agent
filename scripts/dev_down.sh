#!/usr/bin/env bash
# ==============================================================================
# scripts/dev_down.sh
# ------------------------------------------------------------------------------
# Tear down the local dev stack started by scripts/dev_up.sh and re-enable the
# VPS services that were paused during hot-swap.
#
# Order matters:
#   1. Stop local processes first (so nothing is still long-polling Telegram,
#      listening on Discord, or pulling from Redis).
#   2. Then SSH to the VPS, delete the pause stamp, and start the conflict
#      services back up.
#
# This script is safe to run even if dev_up.sh was not run (idempotent).
# ==============================================================================
set -Eeuo pipefail

# ------------------------------------------------------------------------------
# Keep this list in sync with scripts/dev_up.sh.
# ------------------------------------------------------------------------------
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

VPS_SSH_HOST="${UA_VPS_SSH_HOST:-root@uaonvps}"
VPS_SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
VPS_PAUSE_STAMP_PATH="${UA_VPS_PAUSE_STAMP_PATH:-/etc/universal-agent/dev_pause.stamp}"
VPS_SKIP_RESUME="${UA_VPS_SKIP_RESUME:-0}"

if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'
  C_CYA=$'\033[36m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_RED=""; C_YEL=""; C_GRN=""; C_CYA=""; C_BLD=""; C_RST=""
fi
say()  { printf '%s[dev_down]%s %s\n' "$C_CYA" "$C_RST" "$*"; }
warn() { printf '%s[dev_down]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }

VPS_BANNER_STATE="unknown"
SAFE_TO_PUSH=0

# ------------------------------------------------------------------------------
# Stop local services via PIDs recorded by dev_up.sh. If PID file is missing
# or processes are already gone, continue to the VPS step anyway.
# ------------------------------------------------------------------------------
stop_local() {
  if [[ ! -f "$PID_FILE" ]]; then
    warn "No PID file at $PID_FILE — assuming local stack is already stopped."
    return 0
  fi

  say "Stopping local services recorded in $PID_FILE"

  local line name pid
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    name="${line%%:*}"
    pid="${line##*:}"
    if [[ -z "$pid" ]]; then
      continue
    fi
    if kill -0 "$pid" >/dev/null 2>&1; then
      say "  sending SIGTERM to $name (pid=$pid)"
      kill -TERM "$pid" >/dev/null 2>&1 || true
    else
      say "  $name (pid=$pid) already exited"
    fi
  done < "$PID_FILE"

  # Give them a moment, then SIGKILL stragglers.
  sleep 2

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    name="${line%%:*}"
    pid="${line##*:}"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      warn "  $name (pid=$pid) did not exit on SIGTERM; sending SIGKILL"
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  done < "$PID_FILE"

  rm -f "$PID_FILE"
  say "Local stack stopped. Logs remain in $LOG_DIR"
}

# ------------------------------------------------------------------------------
# Remove the VPS pause stamp and start the paused units back up. Idempotent.
# ------------------------------------------------------------------------------
vps_resume() {
  if [[ "$VPS_SKIP_RESUME" == "1" ]]; then
    warn "UA_VPS_SKIP_RESUME=1 — not touching VPS. You will need to resume services manually."
    VPS_BANNER_STATE="NOT touched (UA_VPS_SKIP_RESUME=1) — manual resume required"
    SAFE_TO_PUSH=0
    return 0
  fi

  say "Resuming VPS services on $VPS_SSH_HOST"

  local start_cmds=""
  # Start timers AFTER services so the watchdog doesn't race.
  for unit in "${VPS_CONFLICT_SERVICES[@]}"; do
    start_cmds+="systemctl start '$unit' || true; "
  done
  for unit in "${VPS_CONFLICT_TIMERS[@]}"; do
    start_cmds+="systemctl start '$unit' || true; "
  done

  local remote_cmd
  remote_cmd=$(cat <<REMOTE
set -e
rm -f '$VPS_PAUSE_STAMP_PATH'
$start_cmds
echo "pause stamp cleared; services resumed"
REMOTE
)

  if ! ssh -i "$VPS_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 \
        "$VPS_SSH_HOST" "bash -s" <<<"$remote_cmd"; then
    warn "Failed to resume VPS services over SSH."
    warn "You can manually run on the VPS:"
    warn "  rm -f $VPS_PAUSE_STAMP_PATH"
    for unit in "${VPS_CONFLICT_SERVICES[@]}" "${VPS_CONFLICT_TIMERS[@]}"; do
      warn "  systemctl start $unit"
    done
    warn "Or wait for the dev-pause reconciler timer to expire."
    VPS_BANNER_STATE="SSH resume FAILED — see warnings above"
    SAFE_TO_PUSH=0
    return 1
  fi

  say "${C_GRN}VPS services resumed.${C_RST}"
  VPS_BANNER_STATE="resumed on $VPS_SSH_HOST"
  SAFE_TO_PUSH=1
}

print_banner() {
  local push_line
  if (( SAFE_TO_PUSH == 1 )); then
    push_line="${C_GRN} You can now safely push to develop or main.${C_RST}"
  else
    push_line="${C_RED}${C_BLD} DO NOT push to develop or main — VPS is not in a known-good state.${C_RST}"
  fi

  cat <<EOF

${C_GRN}${C_BLD}========================================================================
 Universal Agent — LOCAL DEV STOPPED
========================================================================${C_RST}
 Local services:  stopped
 VPS services:    $VPS_BANNER_STATE
$push_line
${C_GRN}${C_BLD}========================================================================${C_RST}

EOF
}

main() {
  stop_local
  # vps_resume can return non-zero on SSH failure; that's a reported outcome,
  # not a reason to skip the banner. The banner itself communicates the result.
  vps_resume || true
  print_banner
  if (( SAFE_TO_PUSH == 0 )); then
    return 1
  fi
}

main "$@"
