#!/usr/bin/env bash
set -euo pipefail

# Reverse SSH tunnel so the VPS can reach your local UA gateway securely over SSH.
#
# Default behavior:
# - Opens a loopback-only port on the VPS: 127.0.0.1:${REMOTE_PORT}
# - Forwards it to your local gateway: 127.0.0.1:${LOCAL_GATEWAY_PORT}
#
# Then the VPS can POST to:
#   http://127.0.0.1:${REMOTE_PORT}/api/v1/hooks/youtube/manual
#
# Requirements:
# - Your local UA gateway must be running on ${LOCAL_GATEWAY_PORT}.
# - This script must stay running to keep the tunnel alive.

VPS_HOST="${VPS_HOST:-${UA_VPS_HOST:-root@100.106.113.93}}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_PORT="${REMOTE_PORT:-18002}"
LOCAL_GATEWAY_PORT="${LOCAL_GATEWAY_PORT:-8002}"

exec ssh -i "$SSH_KEY" -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -R "127.0.0.1:${REMOTE_PORT}:127.0.0.1:${LOCAL_GATEWAY_PORT}" \
  "$VPS_HOST"
