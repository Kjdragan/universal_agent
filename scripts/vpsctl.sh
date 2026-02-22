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
#   UA_VPS_HOST=root@srv1360701.taildcc090.ts.net
#   UA_SSH_AUTH_MODE=keys|tailscale_ssh
#   UA_VPS_SSH_KEY=~/.ssh/id_ed25519
#   UA_VPS_APP_DIR=/opt/universal_agent

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="${UA_VPS_HOST:-root@srv1360701.taildcc090.ts.net}"
SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_AUTH_MODE="${UA_SSH_AUTH_MODE:-keys}"
REMOTE_DIR="${UA_VPS_APP_DIR:-/opt/universal_agent}"
TAILNET_PREFLIGHT_MODE="${UA_TAILNET_PREFLIGHT:-auto}"
SKIP_TAILNET_PREFLIGHT="${UA_SKIP_TAILNET_PREFLIGHT:-false}"

usage() {
  cat <<'EOF'
Usage: scripts/vpsctl.sh <command> [args...]

Commands:
  push <path...>          Copy repo-relative file(s)/dir(s) to VPS under /opt/universal_agent
  restart <svc|all>       Restart systemd unit(s): gateway|api|webui|telegram|all
  status <svc|all>        Show is-active for unit(s)
  logs <svc>              Tail recent logs for a unit

File Inspection:
  sessions [filter]       List agent session workspaces (optional prefix filter: chat, cron, hook, tg_)
  browse <path>           List files at a VPS path (project-relative or absolute)
  read <path> [lines]     Read a file from VPS (optional: last N lines with tail mode)
  inspect <session_id>    Quick session diagnostics (run.log tail, work products, transcript)

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
  local ssh_args=(ssh)
  if [[ "${SSH_AUTH_MODE}" == "keys" && -n "${SSH_KEY}" ]]; then
    ssh_args+=(-i "$SSH_KEY")
  fi
  "${ssh_args[@]}" "$VPS_HOST" "$@"
}

scp_vps() {
  local scp_args=(scp)
  if [[ "${SSH_AUTH_MODE}" == "keys" && -n "${SSH_KEY}" ]]; then
    scp_args+=(-i "$SSH_KEY")
  fi
  "${scp_args[@]}" "$@"
}

run_tailnet_preflight() {
  local host_only="${VPS_HOST#*@}"
  local tailnet_host="false"
  case "${host_only}" in
    *.tail*.ts.net|100.*) tailnet_host="true" ;;
  esac

  local should_run="false"
  case "$(printf '%s' "${TAILNET_PREFLIGHT_MODE}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|force|required) should_run="true" ;;
    0|false|no|off|disabled) should_run="false" ;;
    *) [[ "${tailnet_host}" == "true" ]] && should_run="true" ;;
  esac
  case "$(printf '%s' "${SKIP_TAILNET_PREFLIGHT}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) should_run="false" ;;
  esac

  if [[ "${should_run}" != "true" ]]; then
    return 0
  fi
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "ERROR: tailscale CLI is required for tailnet preflight." >&2
    echo "Set UA_TAILNET_PREFLIGHT=off (or UA_SKIP_TAILNET_PREFLIGHT=true) for break-glass bypass." >&2
    exit 1
  fi
  if ! tailscale status >/dev/null 2>&1; then
    echo "ERROR: tailscale status check failed." >&2
    exit 1
  fi
  if ! tailscale ping "${host_only}" >/dev/null 2>&1; then
    echo "ERROR: tailscale ping failed for ${host_only}." >&2
    exit 1
  fi
}

cmd="${1:-}"
shift || true

case "$(printf '%s' "${SSH_AUTH_MODE}" | tr '[:upper:]' '[:lower:]')" in
  keys)
    SSH_AUTH_MODE="keys"
    if [[ -n "${SSH_KEY}" && ! -f "${SSH_KEY}" ]]; then
      echo "ERROR: SSH key does not exist: ${SSH_KEY}" >&2
      exit 1
    fi
    ;;
  tailscale_ssh)
    SSH_AUTH_MODE="tailscale_ssh"
    SSH_KEY=""
    ;;
  *)
    echo "ERROR: UA_SSH_AUTH_MODE must be keys or tailscale_ssh. Got: ${SSH_AUTH_MODE}" >&2
    exit 1
    ;;
esac

case "$cmd" in
  -h|--help|help|"")
    ;;
  *)
    run_tailnet_preflight
    ;;
esac

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

  # =========================================================================
  # File Inspection Commands
  # =========================================================================
  sessions)
    filter="${1:-}"
    ssh_vps "cd '$REMOTE_DIR/AGENT_RUN_WORKSPACES' && for d in \$(ls -1td */ 2>/dev/null | head -40); do
      d=\${d%/}
      if [ -n '$filter' ] && [ '$filter' != '' ]; then
        case \$d in $filter*) ;; *) continue ;; esac
      fi
      mod=\$(stat -c '%y' \"\$d\" 2>/dev/null | cut -d. -f1)
      files=\$(find \"\$d\" -type f 2>/dev/null | wc -l)
      wp=0
      [ -d \"\$d/work_products\" ] && wp=\$(find \"\$d/work_products\" -type f 2>/dev/null | wc -l)
      printf '%-55s  %s  files=%-4s wp=%s\n' \"\$d\" \"\$mod\" \"\$files\" \"\$wp\"
    done"
    ;;
  browse)
    target="${1:-}"
    if [ -z "$target" ]; then
      echo "ERROR: browse requires a path (project-relative or absolute)"
      exit 2
    fi
    # If not absolute, treat as project-relative
    if [[ "$target" != /* ]]; then
      target="$REMOTE_DIR/$target"
    fi
    ssh_vps "ls -lah '$target' 2>&1 || echo 'Path not found: $target'"
    ;;
  read)
    target="${1:-}"
    tail_n="${2:-}"
    if [ -z "$target" ]; then
      echo "ERROR: read requires a file path"
      exit 2
    fi
    if [[ "$target" != /* ]]; then
      target="$REMOTE_DIR/$target"
    fi
    if [ -n "$tail_n" ]; then
      ssh_vps "tail -n '$tail_n' '$target' 2>&1 || echo 'File not found: $target'"
    else
      ssh_vps "cat '$target' 2>&1 || echo 'File not found: $target'"
    fi
    ;;
  inspect)
    sid="${1:-}"
    if [ -z "$sid" ]; then
      echo "ERROR: inspect requires a session_id"
      exit 2
    fi
    ssh_vps "
      ws='$REMOTE_DIR/AGENT_RUN_WORKSPACES/$sid'
      if [ ! -d \"\$ws\" ]; then echo 'Session not found: $sid'; exit 1; fi
      echo '=== SESSION: $sid ==='
      echo ''
      echo '--- Files ---'
      find \"\$ws\" -type f -printf '%T+ %s %P\n' 2>/dev/null | sort -r | head -30
      echo ''
      if [ -f \"\$ws/run.log\" ]; then
        echo '--- run.log (last 60 lines) ---'
        tail -n 60 \"\$ws/run.log\"
        echo ''
      fi
      if [ -d \"\$ws/work_products\" ]; then
        echo '--- work_products ---'
        find \"\$ws/work_products\" -type f -printf '%s\t%P\n' 2>/dev/null | sort -rn | head -20
        echo ''
      fi
      if [ -f \"\$ws/transcript.md\" ]; then
        echo '--- transcript.md (last 40 lines) ---'
        tail -n 40 \"\$ws/transcript.md\"
      fi
    "
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
