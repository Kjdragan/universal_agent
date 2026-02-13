#!/usr/bin/env bash
set -euo pipefail

# Local dev entrypoint that starts:
# - reverse SSH tunnel (VPS loopback -> local gateway) for YouTube hook mirroring
# - UA "gateway mode" stack (gateway + api + web ui) via ./start_gateway.sh
#
# Usage:
#   ./start_local_dev_with_youtube_forwarding.sh
#   ./start_local_dev_with_youtube_forwarding.sh --no-tunnel
#   ./start_local_dev_with_youtube_forwarding.sh --tunnel-only
#   ./start_local_dev_with_youtube_forwarding.sh -- --no-browser
#   ./start_local_dev_with_youtube_forwarding.sh -- --server
#
# Notes:
# - Best practice: install/enable the systemd user unit so the tunnel auto-reconnects.
#   See: scripts/install_ua_youtube_forward_tunnel_user_service.sh

cd "$(dirname "$0")"

WITH_TUNNEL=1
TUNNEL_ONLY=0
STACK_ONLY=0
USE_SYSTEMD=1
PASS_ARGS=()

print_usage() {
  cat <<'EOF'
Usage: ./start_local_dev_with_youtube_forwarding.sh [options] [-- <start_gateway.sh args>]

Options:
  --no-tunnel        Do not start the reverse tunnel
  --tunnel-only      Start tunnel and exit
  --stack-only       Start UA stack only (no tunnel)
  --no-systemd       Do not use systemd even if available (fallback background ssh)
  --help             Show this help

Examples:
  ./start_local_dev_with_youtube_forwarding.sh
  ./start_local_dev_with_youtube_forwarding.sh -- --no-browser
  ./start_local_dev_with_youtube_forwarding.sh --tunnel-only
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-tunnel)
      WITH_TUNNEL=0
      STACK_ONLY=1
      shift
      ;;
    --tunnel-only)
      TUNNEL_ONLY=1
      shift
      ;;
    --stack-only)
      STACK_ONLY=1
      WITH_TUNNEL=0
      shift
      ;;
    --no-systemd)
      USE_SYSTEMD=0
      shift
      ;;
    --help|-h)
      print_usage
      exit 0
      ;;
    --)
      shift
      while [ $# -gt 0 ]; do
        PASS_ARGS+=("$1")
        shift
      done
      break
      ;;
    *)
      PASS_ARGS+=("$1")
      shift
      ;;
  esac
done

start_tunnel_via_systemd_if_available() {
  if [ "$USE_SYSTEMD" -ne 1 ]; then
    return 1
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi
  # systemd user instance not always available (e.g., non-interactive shells)
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    return 1
  fi
  if ! systemctl --user cat ua-youtube-forward-tunnel.service >/dev/null 2>&1; then
    return 1
  fi

  echo "Starting tunnel via systemd user unit: ua-youtube-forward-tunnel.service"
  systemctl --user start ua-youtube-forward-tunnel.service
  systemctl --user --no-pager --full status ua-youtube-forward-tunnel.service || true
  return 0
}

start_tunnel_fallback_background() {
  local run_dir=".run"
  local pid_file="$run_dir/ua_youtube_tunnel.pid"
  local log_file="$run_dir/ua_youtube_tunnel.log"
  mkdir -p "$run_dir"

  if [ -f "$pid_file" ]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      echo "Tunnel already running (pid=$pid)."
      return 0
    fi
  fi

  echo "Starting tunnel in background (fallback, no auto-reconnect unless you use systemd)."
  nohup ./scripts/forward_youtube_hooks_to_local.sh >>"$log_file" 2>&1 &
  echo "$!" >"$pid_file"
  echo "Tunnel started (pid=$(cat "$pid_file")). Logs: $log_file"
}

start_tunnel_best_effort() {
  if start_tunnel_via_systemd_if_available; then
    return 0
  fi
  start_tunnel_fallback_background
}

if [ "$WITH_TUNNEL" -eq 1 ] || [ "$TUNNEL_ONLY" -eq 1 ]; then
  start_tunnel_best_effort
fi

if [ "$TUNNEL_ONLY" -eq 1 ]; then
  exit 0
fi

if [ ! -x ./start_gateway.sh ]; then
  echo "ERROR: ./start_gateway.sh not found or not executable."
  exit 1
fi

exec ./start_gateway.sh "${PASS_ARGS[@]}"

