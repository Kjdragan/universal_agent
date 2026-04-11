#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Universal Agent — Local Dev Stack (stop)
# ============================================================================

PID_FILE="/tmp/ua-local-dev.pids"
LOG_DIR="/tmp/ua-local-logs"

banner() {
  echo ""
  echo "=========================================="
  echo " universal_agent — local dev stack"
  echo "  start:  ./scripts/dev_up.sh"
  echo "  stop:   ./scripts/dev_down.sh  (this)"
  echo "  status: ./scripts/dev_status.sh"
  echo "  reset:  ./scripts/dev_reset.sh"
  echo "=========================================="
  echo ""
}

usage() {
  banner
  echo "Usage: ./scripts/dev_down.sh [--help]"
  echo ""
  echo "Stops all locally running Universal Agent services."
  echo "Does NOT touch VPS or remote systems."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || "${1:-}" == "help" ]]; then
  usage
  exit 0
fi

banner

if [[ ! -f "$PID_FILE" ]]; then
  echo "No PID file found at ${PID_FILE}."
  echo "Nothing to stop."
  exit 0
fi

_any_stopped=false

while IFS=: read -r pid name; do
  [[ -z "$pid" ]] && continue
  if kill -0 "$pid" 2>/dev/null; then
    echo "Stopping ${name} (PID ${pid})..."
    kill "$pid" 2>/dev/null || true
    # Wait up to 5 seconds for graceful shutdown
    for _i in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    # Force kill if still alive
    if kill -0 "$pid" 2>/dev/null; then
      echo "  Force-killing ${name} (PID ${pid})..."
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "  ✓ ${name} stopped"
    _any_stopped=true
  else
    echo "  ${name} (PID ${pid}) already dead"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"

# Verify ports are free
echo ""
_all_free=true
for port in 8001 8002 3000; do
  if lsof -i ":$port" &>/dev/null 2>&1; then
    echo "  ⚠ Port ${port} still in use"
    _all_free=false
  else
    echo "  ✓ Port ${port} free"
  fi
done

echo ""
if [[ "$_all_free" == "true" ]]; then
  echo "All services stopped. Ports freed."
else
  echo "Some ports are still in use. You may need to manually kill processes."
fi

if [[ "$_any_stopped" == "false" ]]; then
  echo "No running services were found."
fi

echo ""
echo "To start again: ./scripts/dev_up.sh"
