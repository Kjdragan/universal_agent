#!/usr/bin/env bash
set -euo pipefail

# Minimal, agent-safe VPS helper (scp/ssh only).
#
# Examples:
#   scripts/vpsctl.sh push src/universal_agent/hooks_service.py
#   scripts/vpsctl.sh restart gateway
#   scripts/vpsctl.sh restart all
#   scripts/vpsctl.sh logs gateway
#
# Config via env:
#   UA_VPS_HOST=root@187.77.16.29
#   UA_VPS_SSH_KEY=~/.ssh/id_ed25519
#   UA_VPS_APP_DIR=/opt/universal_agent

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="${UA_VPS_HOST:-root@187.77.16.29}"
SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${UA_VPS_APP_DIR:-/opt/universal_agent}"

usage() {
  cat <<'EOF'
Usage: scripts/vpsctl.sh <command> [args...]

Commands:
  push <path...>          Copy repo-relative file(s)/dir(s) to VPS under /opt/universal_agent
  restart <svc|all>       Restart systemd unit(s): gateway|api|webui|telegram|all
  status <svc|all>        Show is-active for unit(s)
  logs <svc>              Tail recent logs for a unit

Services:
  gateway -> universal-agent-gateway
  api     -> universal-agent-api
  webui   -> universal-agent-webui
  telegram-> universal-agent-telegram
EOF
}

unit_for() {
  case "${1:-}" in
    gateway) echo "universal-agent-gateway" ;;
    api) echo "universal-agent-api" ;;
    webui) echo "universal-agent-webui" ;;
    telegram) echo "universal-agent-telegram" ;;
    *)
      echo "ERROR: unknown service '$1'" >&2
      exit 2
      ;;
  esac
}

ssh_vps() {
  ssh -i "$SSH_KEY" "$VPS_HOST" "$@"
}

scp_vps() {
  scp -i "$SSH_KEY" "$@"
}

cmd="${1:-}"
shift || true

case "$cmd" in
  push)
    if [ $# -lt 1 ]; then
      echo "ERROR: push requires at least one path"
      exit 2
    fi
    for p in "$@"; do
      abs="$REPO_ROOT/$p"
      if [ ! -e "$abs" ]; then
        echo "ERROR: not found: $p"
        exit 2
      fi
      remote="$REMOTE_DIR/$p"
      remote_dir="$(dirname "$remote")"
      ssh_vps "mkdir -p '$remote_dir'"
      if [ -d "$abs" ]; then
        scp_vps -r "$abs" "$VPS_HOST:$remote"
      else
        scp_vps "$abs" "$VPS_HOST:$remote"
      fi
      echo "PUSHED $p -> $remote"
    done
    ;;
  restart)
    target="${1:-}"
    if [ -z "$target" ]; then
      echo "ERROR: restart requires a service or 'all'"
      exit 2
    fi
    if [ "$target" = "all" ]; then
      ssh_vps "systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram"
      ssh_vps "systemctl is-active universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram || true"
    else
      unit="$(unit_for "$target")"
      ssh_vps "systemctl restart '$unit' && systemctl is-active '$unit' && systemctl status '$unit' --no-pager -n 40"
    fi
    ;;
  status)
    target="${1:-}"
    if [ -z "$target" ] || [ "$target" = "all" ]; then
      ssh_vps "for s in universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram; do printf '%s=' \"\$s\"; systemctl is-active \"\$s\" || true; done"
    else
      unit="$(unit_for "$target")"
      ssh_vps "systemctl is-active '$unit' || true; systemctl status '$unit' --no-pager -n 40 || true"
    fi
    ;;
  logs)
    target="${1:-}"
    if [ -z "$target" ]; then
      echo "ERROR: logs requires a service"
      exit 2
    fi
    unit="$(unit_for "$target")"
    ssh_vps "journalctl -u '$unit' -n 220 --no-pager"
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "ERROR: unknown command '$cmd'"
    usage
    exit 2
    ;;
esac

