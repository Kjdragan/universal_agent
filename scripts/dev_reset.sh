#!/usr/bin/env bash
# ==============================================================================
# scripts/dev_reset.sh
# ------------------------------------------------------------------------------
# Destructive: wipes the local dev data directory (DBs, artifacts, logs).
# Refuses to run if local dev services are still active. Requires an explicit
# confirmation phrase to avoid accidents.
#
# Usage:
#   scripts/dev_reset.sh                # interactive, asks for "wipe it"
#   CONFIRM="wipe it" scripts/dev_reset.sh  # non-interactive
# ==============================================================================
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCAL_DATA_DIR="${UA_LOCAL_DATA_DIR:-$HOME/lrepos/universal_agent_local_data}"
LOG_DIR="${UA_LOCAL_LOG_DIR:-/tmp/ua-local-logs}"
PID_FILE="${UA_LOCAL_PID_FILE:-/tmp/ua-local-dev.pids}"

if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'
  C_CYA=$'\033[36m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_RED=""; C_YEL=""; C_GRN=""; C_CYA=""; C_BLD=""; C_RST=""
fi
say()  { printf '%s[dev_reset]%s %s\n' "$C_CYA" "$C_RST" "$*"; }
warn() { printf '%s[dev_reset]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }
die()  { printf '%s[dev_reset] ERROR:%s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

# Refuse if the local stack is still running.
if [[ -f "$PID_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    pid="${line##*:}"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      die "Local dev services are still running (see $PID_FILE). Run scripts/dev_down.sh first."
    fi
  done < "$PID_FILE"
fi

if [[ ! -d "$LOCAL_DATA_DIR" && ! -d "$LOG_DIR" ]]; then
  say "Nothing to reset. Data dir and log dir do not exist."
  exit 0
fi

cat <<EOF

${C_RED}${C_BLD}WARNING — this will permanently delete:${C_RST}
  $LOCAL_DATA_DIR    (runtime, coder_vp, vp, activity, lossless DBs + artifacts)
  $LOG_DIR           (local service logs)

It will NOT touch anything on the VPS, nor any Infisical secrets.

EOF

CONFIRM_INPUT="${CONFIRM:-}"
if [[ -z "$CONFIRM_INPUT" ]]; then
  if [[ ! -t 0 ]]; then
    die "stdin is not a TTY. Set CONFIRM='wipe it' to run non-interactively."
  fi
  read -r -p "Type 'wipe it' to confirm: " CONFIRM_INPUT
fi

if [[ "$CONFIRM_INPUT" != "wipe it" ]]; then
  die "Confirmation mismatch. Aborted."
fi

if [[ -d "$LOCAL_DATA_DIR" ]]; then
  say "Removing $LOCAL_DATA_DIR"
  rm -rf -- "$LOCAL_DATA_DIR"
fi

if [[ -d "$LOG_DIR" ]]; then
  say "Removing $LOG_DIR"
  rm -rf -- "$LOG_DIR"
fi

say "${C_GRN}Local dev state wiped.${C_RST} Next scripts/dev_up.sh will start fresh."
