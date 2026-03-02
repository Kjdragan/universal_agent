#!/usr/bin/env bash
# VPS deployment script (local workspace -> remote app dir).
# Usage: ./scripts/deploy_vps.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="${UA_VPS_HOST:-root@srv1360701.taildcc090.ts.net}"
SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_AUTH_MODE="${UA_SSH_AUTH_MODE:-keys}"
REMOTE_DIR="${UA_VPS_APP_DIR:-/opt/universal_agent}"
TAILNET_PREFLIGHT_MODE="${UA_TAILNET_PREFLIGHT:-auto}"
SKIP_TAILNET_PREFLIGHT="${UA_SKIP_TAILNET_PREFLIGHT:-false}"
TAILNET_STAGING_MODE="${UA_TAILNET_STAGING_MODE:-auto}"
DEPLOY_CONFIGURE_SWAP="${UA_DEPLOY_CONFIGURE_SWAP:-true}"
DEPLOY_MEMORY_GUARDRAILS="${UA_DEPLOY_MEMORY_GUARDRAILS:-true}"
DEPLOY_OOM_ALERT_TIMER="${UA_DEPLOY_OOM_ALERT_TIMER:-true}"
DEPLOY_TUTORIAL_REPO_ROOT="${UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT:-/home/kjdragan/YoutubeCodeExamples}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "ERROR: rsync is required for deployment."
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ERROR: ssh is required for deployment."
  exit 1
fi

case "$(printf '%s' "${SSH_AUTH_MODE}" | tr '[:upper:]' '[:lower:]')" in
  keys)
    SSH_AUTH_MODE="keys"
    ;;
  tailscale_ssh)
    SSH_AUTH_MODE="tailscale_ssh"
    SSH_KEY=""
    ;;
  *)
    echo "ERROR: UA_SSH_AUTH_MODE must be keys or tailscale_ssh. Got: ${SSH_AUTH_MODE}"
    exit 1
    ;;
esac

if [[ "${SSH_AUTH_MODE}" == "keys" && -n "${SSH_KEY}" && ! -f "${SSH_KEY}" ]]; then
  echo "ERROR: SSH key does not exist: ${SSH_KEY}"
  exit 1
fi

host_only="${VPS_HOST#*@}"
case "${host_only}" in
  *.tail*.ts.net|100.*) tailnet_host="true" ;;
  *) tailnet_host="false" ;;
esac

should_run_tailnet_preflight="false"
case "$(printf '%s' "${TAILNET_PREFLIGHT_MODE}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on|force|required)
    should_run_tailnet_preflight="true"
    ;;
  0|false|no|off|disabled)
    should_run_tailnet_preflight="false"
    ;;
  *)
    if [[ "${tailnet_host}" == "true" ]]; then
      should_run_tailnet_preflight="true"
    fi
    ;;
esac

case "$(printf '%s' "${SKIP_TAILNET_PREFLIGHT}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    should_run_tailnet_preflight="false"
    ;;
esac

if [[ "${should_run_tailnet_preflight}" == "true" ]]; then
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "ERROR: tailscale CLI is required for tailnet preflight."
    echo "Set UA_TAILNET_PREFLIGHT=off (or UA_SKIP_TAILNET_PREFLIGHT=true) for break-glass bypass."
    exit 1
  fi
  echo "Running tailnet preflight for ${host_only}..."
  if ! tailscale status >/dev/null 2>&1; then
    echo "ERROR: tailscale status check failed."
    exit 1
  fi
  if ! tailscale ping "${host_only}" >/dev/null 2>&1; then
    echo "ERROR: tailscale ping failed for ${host_only}."
    exit 1
  fi
  echo "Tailnet preflight passed."
fi

echo "Deploying local HEAD to VPS"
echo "Host: $VPS_HOST"
echo "SSH auth mode: $SSH_AUTH_MODE"
echo "Remote dir: $REMOTE_DIR"
echo "Local commit: $(cd "$REPO_ROOT" && git rev-parse --short HEAD)"

ssh_base=(ssh -o StrictHostKeyChecking=no)
if [[ "${SSH_AUTH_MODE}" == "keys" && -n "${SSH_KEY}" ]]; then
  ssh_base+=(-i "$SSH_KEY")
fi

# Pre-flight check: Verify connectivity before attempting deployment
echo "Checking connectivity to $VPS_HOST..."
if ! "${ssh_base[@]}" -q -o BatchMode=yes -o ConnectTimeout=10 "$VPS_HOST" "echo 'Connection established'" >/dev/null 2>&1; then
  echo "ERROR: Cannot connect to $VPS_HOST."
  echo "  - Check if Tailscale is up (if using VPN)"
  if [[ "${SSH_AUTH_MODE}" == "keys" ]]; then
    echo "  - Check if SSH key is valid: $SSH_KEY"
  fi
  echo "  - Check if host is online"
  exit 1
fi
echo "Connectivity confirmed."

rsync_ssh=(ssh -o StrictHostKeyChecking=no)
if [[ "${SSH_AUTH_MODE}" == "keys" && -n "${SSH_KEY}" ]]; then
  rsync_ssh+=(-i "$SSH_KEY")
fi
RSYNC_RSH="$(printf '%q ' "${rsync_ssh[@]}")"

# Sync tracked project content while preserving runtime secrets/state on VPS.
rsync -az \
  --exclude ".git/" \
  --exclude ".venv/" \
  --exclude ".env" \
  --exclude "AGENT_RUN_WORKSPACES/" \
  --exclude "artifacts/" \
  --exclude "tmp/" \
  --exclude "web-ui/.next/" \
  --exclude "web-ui/node_modules/" \
  --exclude "test-remotion-project/node_modules/" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  -e "$RSYNC_RSH" \
  "$REPO_ROOT/" "$VPS_HOST:$REMOTE_DIR/"

"${ssh_base[@]}" "$VPS_HOST" "
  set -euo pipefail
  cd '$REMOTE_DIR'

  require_env_key() {
    key=\"\$1\"
    value=\$(grep -E \"^\${key}=\" .env | tail -n1 | cut -d= -f2- || true)
    if [ -z \"\$value\" ]; then
      echo \"ERROR: missing required env key \${key} in $REMOTE_DIR/.env\" >&2
      exit 42
    fi
    printf '%s=%s\n' \"\$key\" \"\$value\"
  }

  # Keep source/build files owned by service user for build/runtime writes.
  chown -R ua:ua .claude deployment docs OFFICIAL_PROJECT_DOCUMENTATION scripts src tests web-ui webhook_transforms 2>/dev/null || true
  chown ua:ua pyproject.toml uv.lock README.md AGENTS.md .gitignore 2>/dev/null || true
  # Runtime roots must be writable by service user for memory/session capture.
  mkdir -p Memory_System AGENT_RUN_WORKSPACES artifacts logs
  chown -R ua:ua Memory_System AGENT_RUN_WORKSPACES artifacts logs 2>/dev/null || true
  # Process heartbeat directory for watchdog liveness detection.
  mkdir -p /var/lib/universal-agent/heartbeat
  chown ua:ua /var/lib/universal-agent/heartbeat 2>/dev/null || true
  # Tutorial bootstrap target root (used by dashboard "Create Repo" action).
  mkdir -p '${DEPLOY_TUTORIAL_REPO_ROOT}'
  chown -R ua:ua '${DEPLOY_TUTORIAL_REPO_ROOT}' 2>/dev/null || true

  if [ ! -f .env ]; then
    echo 'ERROR: .env is required on VPS for runtime + VP worker configuration.' >&2
    exit 41
  fi
  chown root:ua .env
  chmod 640 .env

  echo '== Runtime prerequisites =='
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null
  apt-get install -y sqlite3 >/dev/null
  if [ ! -x /home/ua/.local/bin/uv ]; then
    runuser -u ua -- bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  fi
  if [ -x /home/ua/.local/bin/uv ]; then
    ln -sf /home/ua/.local/bin/uv /usr/local/bin/uv
  fi
  command -v uv >/dev/null 2>&1
  command -v sqlite3 >/dev/null 2>&1

  echo '== Infisical SDK toolchain =='
  chmod 0755 scripts/install_vps_infisical_sdk.sh
  APP_ROOT='$REMOTE_DIR' APP_USER='ua' bash scripts/install_vps_infisical_sdk.sh --prepare-only

  echo '== Resilience hardening =='
  chmod 0755 scripts/install_vps_swap.sh scripts/install_vps_memory_guardrails.sh scripts/install_vps_oom_alert.sh scripts/watchdog_oom_notifier.py
  case \"\$(printf '%s' '${DEPLOY_CONFIGURE_SWAP}' | tr '[:upper:]' '[:lower:]')\" in
    0|false|no|off)
      echo 'Swap install disabled by UA_DEPLOY_CONFIGURE_SWAP.'
      ;;
    *)
      APP_ROOT='$REMOTE_DIR' bash scripts/install_vps_swap.sh
      ;;
  esac
  case \"\$(printf '%s' '${DEPLOY_MEMORY_GUARDRAILS}' | tr '[:upper:]' '[:lower:]')\" in
    0|false|no|off)
      echo 'Memory guardrails disabled by UA_DEPLOY_MEMORY_GUARDRAILS.'
      ;;
    *)
      APP_ROOT='$REMOTE_DIR' bash scripts/install_vps_memory_guardrails.sh
      ;;
  esac
  case \"\$(printf '%s' '${DEPLOY_OOM_ALERT_TIMER}' | tr '[:upper:]' '[:lower:]')\" in
    0|false|no|off)
      echo 'OOM alert timer install disabled by UA_DEPLOY_OOM_ALERT_TIMER.'
      ;;
    *)
      APP_ROOT='$REMOTE_DIR' bash scripts/install_vps_oom_alert.sh
      ;;
  esac

  echo '== Python deps =='
  runuser -u ua -- bash -lc 'export PATH=\"/home/ua/.local/bin:/home/ua/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH\"; export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1; cd $REMOTE_DIR && uv sync'

  echo '== Infisical SDK verification =='
  APP_ROOT='$REMOTE_DIR' APP_USER='ua' bash scripts/install_vps_infisical_sdk.sh --verify-only

  echo '== Web UI build =='
  if [ -d web-ui ] && command -v npm >/dev/null 2>&1; then
    runuser -u ua -- bash -lc 'cd $REMOTE_DIR/web-ui && npm install && npm run build'
  else
    echo 'WARN: web-ui or npm missing, skipping web build'
  fi

  echo '== VP env validation (strict) =='
  vp_dispatch_enabled=\$(require_env_key UA_VP_EXTERNAL_DISPATCH_ENABLED | cut -d= -f2- | tr '[:upper:]' '[:lower:]')
  vp_dispatch_mode=\$(require_env_key UA_VP_DISPATCH_MODE | cut -d= -f2- | tr '[:upper:]' '[:lower:]')
  vp_enabled_ids=\$(require_env_key UA_VP_ENABLED_IDS | cut -d= -f2-)
  case \"\$vp_dispatch_enabled\" in
    1|true|yes|on) ;;
    *)
      echo 'ERROR: UA_VP_EXTERNAL_DISPATCH_ENABLED must be enabled for production VP independence.' >&2
      exit 43
      ;;
  esac
  if [ \"\$vp_dispatch_mode\" != 'db_pull' ]; then
    echo \"ERROR: UA_VP_DISPATCH_MODE must be 'db_pull' (got \$vp_dispatch_mode).\" >&2
    exit 44
  fi
  case \",\$vp_enabled_ids,\" in
    *,vp.general.primary,* ) ;;
    *)
      echo 'ERROR: UA_VP_ENABLED_IDS must include vp.general.primary.' >&2
      exit 45
      ;;
  esac
  case \",\$vp_enabled_ids,\" in
    *,vp.coder.primary,* ) ;;
    *)
      echo 'ERROR: UA_VP_ENABLED_IDS must include vp.coder.primary.' >&2
      exit 46
      ;;
  esac

  echo '== Install/refresh VP worker services =='
  chmod 0755 scripts/start_vp_worker.sh scripts/install_vp_worker_services.sh scripts/configure_tailnet_staging.sh
  APP_ROOT='$REMOTE_DIR' bash scripts/install_vp_worker_services.sh vp.general.primary vp.coder.primary

  echo '== Restart services =='
  systemctl restart \
    universal-agent-gateway \
    universal-agent-api \
    universal-agent-webui \
    universal-agent-telegram \
    universal-agent-vp-worker@vp.general.primary \
    universal-agent-vp-worker@vp.coder.primary
  echo '== Service status (bounded wait for active) =='
  services=(\
    universal-agent-gateway \
    universal-agent-api \
    universal-agent-webui \
    universal-agent-telegram \
    universal-agent-vp-worker@vp.general.primary \
    universal-agent-vp-worker@vp.coder.primary \
  )
  max_attempts=12
  sleep_seconds=5
  for s in \"\${services[@]}\"; do
    attempt=1
    while true; do
      status=\$(systemctl is-active \"\$s\" || true)
      if [ \"\$status\" = 'active' ]; then
        printf '%s=%s\n' \"\$s\" \"\$status\"
        break
      fi
      if [ \"\$attempt\" -ge \"\$max_attempts\" ]; then
        echo \"ERROR: service did not reach active state: \$s (last status=\$status)\" >&2
        systemctl status \"\$s\" --no-pager -n 60 || true
        exit 51
      fi
      echo \"WARN: waiting for service to become active: \$s (status=\$status, attempt=\$attempt/\$max_attempts)\"
      attempt=\$((attempt + 1))
      sleep \"\$sleep_seconds\"
    done
  done

  echo
  echo '== Tailnet staging setup =='
  tailnet_staging_mode='${TAILNET_STAGING_MODE}'
  case \"\$(printf '%s' \"\$tailnet_staging_mode\" | tr '[:upper:]' '[:lower:]')\" in
    0|false|no|off|disabled)
      echo 'Tailnet staging mode disabled; skipping setup.'
      ;;
    *)
      if command -v tailscale >/dev/null 2>&1; then
        if bash scripts/configure_tailnet_staging.sh --ensure; then
          echo 'Tailnet staging setup complete.'
        else
          case \"\$(printf '%s' \"\$tailnet_staging_mode\" | tr '[:upper:]' '[:lower:]')\" in
            required|force|strict)
              echo 'ERROR: tailnet staging setup failed in required mode.' >&2
              exit 49
              ;;
            *)
              echo 'WARN: tailnet staging setup failed (non-strict mode); continuing deploy.' >&2
              ;;
          esac
        fi
      else
        case \"\$(printf '%s' \"\$tailnet_staging_mode\" | tr '[:upper:]' '[:lower:]')\" in
          required|force|strict)
            echo 'ERROR: tailscale command not found but tailnet staging mode is required.' >&2
            exit 50
            ;;
          *)
            echo 'WARN: tailscale command not found; skipping tailnet staging setup.' >&2
            ;;
        esac
      fi
      ;;
  esac

  echo
  echo '== VP session readiness =='
  vp_db=\$(grep -E '^UA_VP_DB_PATH=' .env | tail -n1 | cut -d= -f2- || true)
  if [ -z \"\$vp_db\" ]; then
    vp_db='$REMOTE_DIR/AGENT_RUN_WORKSPACES/vp_state.db'
  fi
  if [ ! -f \"\$vp_db\" ]; then
    echo \"ERROR: VP state DB not found at \$vp_db\" >&2
    exit 47
  fi
  sqlite3 \"\$vp_db\" \"SELECT vp_id, status, session_id, lease_owner FROM vp_sessions WHERE vp_id IN ('vp.general.primary','vp.coder.primary') ORDER BY vp_id;\" || true
  for vp in vp.general.primary vp.coder.primary; do
    ready=\$(sqlite3 \"\$vp_db\" \"SELECT COUNT(1) FROM vp_sessions WHERE vp_id='\$vp' AND status IN ('idle','active');\")
    if [ \"\$ready\" = '0' ]; then
      echo \"ERROR: VP worker session not ready for \$vp\" >&2
      exit 48
    fi
  done

  echo
  echo '== Public health =='
  api_code=''
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    api_code=\$(curl -s -o /tmp/deploy_api_health.json -w '%{http_code}' https://api.clearspringcg.com/api/v1/health || true)
    if [ \"\$api_code\" = '200' ]; then break; fi
    sleep 2
  done
  echo \"API=\$api_code\"
  head -c 220 /tmp/deploy_api_health.json || true
  echo
  app_code=''
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    app_code=\$(curl -s -o /dev/null -w '%{http_code}' https://app.clearspringcg.com/ || true)
    if [ \"\$app_code\" = '200' ]; then break; fi
    sleep 2
  done
  echo \"APP=\$app_code\"

  token=\$(grep '^UA_OPS_TOKEN=' .env | tail -n1 | cut -d= -f2- || true)
  if [ -n \"\$token\" ]; then
    echo '== Ops auth check =='
    printf 'OPS_UNAUTH='
    curl -s -o /dev/null -w '%{http_code}' https://api.clearspringcg.com/api/v1/ops/deployment/profile
    echo
    printf 'OPS_AUTH='
    curl -s -o /tmp/deploy_ops_profile.json -w '%{http_code}' -H \"x-ua-ops-token: \$token\" https://api.clearspringcg.com/api/v1/ops/deployment/profile
    echo
    head -c 220 /tmp/deploy_ops_profile.json || true
    echo
  fi
"

echo "Deployment completed."
