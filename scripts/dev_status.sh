#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Universal Agent — Local Dev Stack (status)
# ============================================================================

PID_FILE="/tmp/ua-local-dev.pids"
LOG_DIR="/tmp/ua-local-logs"
LOCAL_DATA_DIR="${UA_LOCAL_DATA_DIR:-$HOME/universal_agent_local_data}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

banner() {
  echo ""
  echo "=========================================="
  echo " universal_agent — local dev stack"
  echo "  start:  ./scripts/dev_up.sh"
  echo "  stop:   ./scripts/dev_down.sh"
  echo "  status: ./scripts/dev_status.sh  (this)"
  echo "  reset:  ./scripts/dev_reset.sh"
  echo "=========================================="
  echo ""
}

usage() {
  banner
  echo "Usage: ./scripts/dev_status.sh [--help]"
  echo ""
  echo "Read-only status check of the local dev stack."
  echo "Does NOT start, stop, or modify anything."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || "${1:-}" == "help" ]]; then
  usage
  exit 0
fi

banner

# --------------------------------------------------------------------------
# Git status
# --------------------------------------------------------------------------
echo "Git:"
echo "  Branch: $(cd "$REPO_ROOT" && git branch --show-current 2>/dev/null || echo 'unknown')"
echo "  Commit: $(cd "$REPO_ROOT" && git log --oneline -1 2>/dev/null || echo 'unknown')"
echo ""

# --------------------------------------------------------------------------
# Process status
# --------------------------------------------------------------------------
echo "Services:"
if [[ -f "$PID_FILE" ]]; then
  while IFS=: read -r pid name; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      echo "  ✓ ${name} (PID ${pid}) — running"
    else
      echo "  ✗ ${name} (PID ${pid}) — dead (stale PID)"
    fi
  done < "$PID_FILE"
else
  echo "  No PID file found — stack is not running"
fi
echo ""

# --------------------------------------------------------------------------
# Port status
# --------------------------------------------------------------------------
echo "Ports:"
for port_label in "8001:API" "8002:Gateway" "3000:WebUI"; do
  port="${port_label%%:*}"
  label="${port_label##*:}"
  if lsof -i ":$port" &>/dev/null 2>&1; then
    echo "  ✓ :${port} (${label}) — in use"
  else
    echo "  ✗ :${port} (${label}) — free"
  fi
done
echo ""

# --------------------------------------------------------------------------
# Data directory
# --------------------------------------------------------------------------
echo "Data:"
if [[ -d "$LOCAL_DATA_DIR" ]]; then
  _size="$(du -sh "$LOCAL_DATA_DIR" 2>/dev/null | cut -f1 || echo 'unknown')"
  echo "  ${LOCAL_DATA_DIR}: ${_size}"
else
  echo "  ${LOCAL_DATA_DIR}: does not exist"
fi

_ws_dir="${REPO_ROOT}/AGENT_RUN_WORKSPACES"
if [[ -d "$_ws_dir" ]]; then
  _ws_size="$(du -sh "$_ws_dir" 2>/dev/null | cut -f1 || echo 'unknown')"
  echo "  AGENT_RUN_WORKSPACES: ${_ws_size}"
else
  echo "  AGENT_RUN_WORKSPACES: does not exist"
fi
echo ""

# --------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------
echo "Logs:"
if [[ -d "$LOG_DIR" ]]; then
  for logfile in "${LOG_DIR}"/*.log; do
    [[ -f "$logfile" ]] || continue
    _lsize="$(du -sh "$logfile" 2>/dev/null | cut -f1 || echo '?')"
    _lname="$(basename "$logfile")"
    echo "  ${_lname}: ${_lsize}"
  done
  [[ "$(ls -A "$LOG_DIR" 2>/dev/null)" ]] || echo "  (empty)"
else
  echo "  ${LOG_DIR}: does not exist"
fi
