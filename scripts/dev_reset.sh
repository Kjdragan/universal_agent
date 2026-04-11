#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Universal Agent — Local Dev Stack (reset)
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
  echo "  status: ./scripts/dev_status.sh"
  echo "  reset:  ./scripts/dev_reset.sh  (this)"
  echo "=========================================="
  echo ""
}

usage() {
  banner
  echo "Usage: ./scripts/dev_reset.sh [--help]"
  echo ""
  echo "Wipes local dev data and starts fresh. Destructive."
  echo "Deletes:"
  echo "  • ${LOCAL_DATA_DIR}/"
  echo "  • ${REPO_ROOT}/AGENT_RUN_WORKSPACES/ (local SQLite databases)"
  echo "  • ${LOG_DIR}/"
  echo ""
  echo "Does NOT touch VPS, production, or git state."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || "${1:-}" == "help" ]]; then
  usage
  exit 0
fi

banner

# Check if stack is running
if [[ -f "$PID_FILE" ]]; then
  _any_alive=false
  while IFS=: read -r pid name; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      _any_alive=true
      break
    fi
  done < "$PID_FILE"

  if [[ "$_any_alive" == "true" ]]; then
    echo "ERROR: Local dev stack is still running."
    echo "Run ./scripts/dev_down.sh first."
    exit 1
  fi
fi

echo "This will delete:"
echo "  • ${LOCAL_DATA_DIR}/"
echo "  • ${REPO_ROOT}/AGENT_RUN_WORKSPACES/"
echo "  • ${LOG_DIR}/"
echo ""
read -rp "Type 'reset' to confirm: " confirm

if [[ "$confirm" != "reset" ]]; then
  echo "Aborted."
  exit 1
fi

echo ""

if [[ -d "$LOCAL_DATA_DIR" ]]; then
  rm -rf "$LOCAL_DATA_DIR"
  echo "  ✓ Deleted ${LOCAL_DATA_DIR}/"
fi
mkdir -p "$LOCAL_DATA_DIR"
echo "  ✓ Recreated ${LOCAL_DATA_DIR}/ (empty)"

if [[ -d "${REPO_ROOT}/AGENT_RUN_WORKSPACES" ]]; then
  rm -rf "${REPO_ROOT}/AGENT_RUN_WORKSPACES"
  echo "  ✓ Deleted ${REPO_ROOT}/AGENT_RUN_WORKSPACES/"
fi

if [[ -d "$LOG_DIR" ]]; then
  rm -rf "$LOG_DIR"
  echo "  ✓ Deleted ${LOG_DIR}/"
fi
mkdir -p "$LOG_DIR"
echo "  ✓ Recreated ${LOG_DIR}/ (empty)"

rm -f "$PID_FILE"

echo ""
echo "Local data reset. Run ./scripts/dev_up.sh to start fresh."
