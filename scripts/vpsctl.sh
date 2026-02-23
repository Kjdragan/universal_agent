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
  restart <svc|all>       Restart systemd unit(s): gateway|api|webui|telegram|csi|all
  status <svc|all>        Show is-active for unit(s)
  logs <svc>              Tail recent logs for a unit
  run <cmd...>            Run a direct remote command via ssh (no shell operators)
  run-file <local.sh>     Execute a local shell script on VPS via 'bash -s'
  doctor                  Remote health + permission checks for core runtime env files
  fix-perms               Repair env file permissions expected by gateway/csi services

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
  csi     -> csi-ingester
EOF
}

unit_for() {
  case "${1:-}" in
    gateway) echo "universal-agent-gateway" ;;
    api) echo "universal-agent-api" ;;
    webui) echo "universal-agent-webui" ;;
    telegram) echo "universal-agent-telegram" ;;
    csi) echo "csi-ingester" ;;
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

remote_doctor() {
  ssh_vps "
    set -euo pipefail
    ROOT_ENV='$REMOTE_DIR/.env'
    CSI_ENV='$REMOTE_DIR/CSI_Ingester/development/deployment/systemd/csi-ingester.env'
    echo '=== service states ==='
    for s in universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram csi-ingester; do
      printf '%s=' \"\$s\"
      systemctl is-active \"\$s\" || true
    done
    echo
    echo '=== env file permissions ==='
    if [ -f \"\$ROOT_ENV\" ]; then
      stat -c 'ROOT_ENV %n owner=%U group=%G mode=%a' \"\$ROOT_ENV\"
    else
      echo \"ROOT_ENV_MISSING \$ROOT_ENV\"
    fi
    if [ -f \"\$CSI_ENV\" ]; then
      stat -c 'CSI_ENV  %n owner=%U group=%G mode=%a' \"\$CSI_ENV\"
    else
      echo \"CSI_ENV_MISSING \$CSI_ENV\"
    fi
    echo
    echo '=== gateway read check (.env) ==='
    if id -u ua >/dev/null 2>&1 && [ -f \"\$ROOT_ENV\" ]; then
      if runuser -u ua -- test -r \"\$ROOT_ENV\"; then
        echo 'UA_CAN_READ_ROOT_ENV=1'
      else
        echo 'UA_CAN_READ_ROOT_ENV=0'
      fi
    else
      echo 'UA_USER_MISSING_OR_ROOT_ENV_MISSING=1'
    fi
  "
}

remote_fix_perms() {
  ssh_vps "
    set -euo pipefail
    ROOT_ENV='$REMOTE_DIR/.env'
    CSI_ENV='$REMOTE_DIR/CSI_Ingester/development/deployment/systemd/csi-ingester.env'

    [ -f \"\$ROOT_ENV\" ] || { echo \"ERROR: missing \$ROOT_ENV\"; exit 2; }
    if id -u ua >/dev/null 2>&1; then
      chown root:ua \"\$ROOT_ENV\"
      chmod 640 \"\$ROOT_ENV\"
    else
      chmod 600 \"\$ROOT_ENV\"
    fi

    if [ -f \"\$CSI_ENV\" ]; then
      chmod 600 \"\$CSI_ENV\"
    fi

    if systemctl list-unit-files | grep -q '^universal-agent-gateway.service'; then
      systemctl restart universal-agent-gateway
      echo \"GATEWAY_STATUS=\$(systemctl is-active universal-agent-gateway || true)\"
    fi
    if systemctl list-unit-files | grep -q '^csi-ingester.service'; then
      systemctl restart csi-ingester
      echo \"CSI_STATUS=\$(systemctl is-active csi-ingester || true)\"
    fi

    stat -c 'ROOT_ENV %n owner=%U group=%G mode=%a' \"\$ROOT_ENV\"
    if [ -f \"\$CSI_ENV\" ]; then
      stat -c 'CSI_ENV  %n owner=%U group=%G mode=%a' \"\$CSI_ENV\"
    fi
  "
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
      ssh_vps "set -euo pipefail; systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram; if systemctl list-unit-files | grep -q '^csi-ingester.service'; then systemctl restart csi-ingester; fi"
      ssh_vps "for s in universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram csi-ingester; do if systemctl list-unit-files | grep -q \"^\${s}.service\"; then printf '%s=' \"\$s\"; systemctl is-active \"\$s\" || true; fi; done"
    else
      unit="$(unit_for "$target")"
      ssh_vps "systemctl restart '$unit' && systemctl is-active '$unit' && systemctl status '$unit' --no-pager -n 40"
    fi
    ;;
  status)
    target="${1:-}"
    if [ -z "$target" ] || [ "$target" = "all" ]; then
      ssh_vps "for s in universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram csi-ingester; do if systemctl list-unit-files | grep -q \"^\${s}.service\"; then printf '%s=' \"\$s\"; systemctl is-active \"\$s\" || true; fi; done"
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
  run)
    if [ $# -lt 1 ]; then
      echo "ERROR: run requires a command"
      exit 2
    fi
    ssh_vps "$@"
    ;;
  run-file)
    script_file="${1:-}"
    if [ -z "$script_file" ]; then
      echo "ERROR: run-file requires a local script path"
      exit 2
    fi
    abs="$REPO_ROOT/$script_file"
    if [[ "$script_file" = /* ]]; then
      abs="$script_file"
    fi
    if [ ! -f "$abs" ]; then
      echo "ERROR: script not found: $script_file"
      exit 2
    fi
    ssh_vps "bash -s" < "$abs"
    ;;
  doctor)
    remote_doctor
    ;;
  fix-perms)
    remote_fix_perms
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
