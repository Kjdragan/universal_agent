#!/usr/bin/env bash
set -euo pipefail

VPS_HOST="${1:-${UA_VPS_HOST:-root@srv1360701.taildcc090.ts.net}}"
SSH_AUTH_MODE="${UA_SSH_AUTH_MODE:-keys}"
SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"

host_only="${VPS_HOST#*@}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "TAILSCALE_PREFLIGHT_ERROR=tailscale_cli_missing"
  exit 1
fi

echo "TAILSCALE_PREFLIGHT_HOST=${VPS_HOST}"
if ! tailscale status >/dev/null 2>&1; then
  echo "TAILSCALE_PREFLIGHT_ERROR=tailscale_not_connected"
  echo "Run: sudo tailscale up"
  exit 2
fi

echo "TAILSCALE_PREFLIGHT_STATUS=ok"
if ! tailscale ping "${host_only}" >/dev/null 2>&1; then
  echo "TAILSCALE_PREFLIGHT_ERROR=ping_failed"
  echo "Check tailnet auth/session for ${host_only}."
  exit 3
fi

echo "TAILSCALE_PREFLIGHT_PING=ok"

ssh_args=(ssh -o BatchMode=yes -o ConnectTimeout=10)
if [[ "${SSH_AUTH_MODE}" == "keys" ]]; then
  if [[ ! -f "${SSH_KEY}" ]]; then
    echo "TAILSCALE_PREFLIGHT_ERROR=missing_ssh_key:${SSH_KEY}"
    exit 4
  fi
  ssh_args+=(-i "${SSH_KEY}" -o IdentitiesOnly=yes)
fi

if "${ssh_args[@]}" "${VPS_HOST}" "echo TAILSCALE_PREFLIGHT_SSH=ok" >/tmp/ua_tailscale_preflight.out 2>/tmp/ua_tailscale_preflight.err; then
  cat /tmp/ua_tailscale_preflight.out
  rm -f /tmp/ua_tailscale_preflight.out /tmp/ua_tailscale_preflight.err
  exit 0
fi

err_msg="$(cat /tmp/ua_tailscale_preflight.err 2>/dev/null || true)"
rm -f /tmp/ua_tailscale_preflight.out /tmp/ua_tailscale_preflight.err

echo "TAILSCALE_PREFLIGHT_ERROR=ssh_failed"
if [[ "${err_msg}" == *"additional check"* || "${err_msg}" == *"login.tailscale.com"* ]]; then
  echo "TAILSCALE_PREFLIGHT_HINT=interactive_check_required"
  echo "Re-run your SSH command and immediately open the printed login.tailscale.com URL to approve the check."
else
  echo "TAILSCALE_PREFLIGHT_DETAIL=${err_msg}"
fi

exit 5
