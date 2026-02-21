#!/usr/bin/env bash
# VPS deployment script (local workspace -> remote app dir).
# Usage: ./scripts/deploy_vps.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="${UA_VPS_HOST:-root@100.106.113.93}"
SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${UA_VPS_APP_DIR:-/opt/universal_agent}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "ERROR: rsync is required for deployment."
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ERROR: ssh is required for deployment."
  exit 1
fi

echo "Deploying local HEAD to VPS"
echo "Host: $VPS_HOST"
echo "Remote dir: $REMOTE_DIR"
echo "Local commit: $(cd "$REPO_ROOT" && git rev-parse --short HEAD)"

# Pre-flight check: Verify connectivity before attempting deployment
echo "Checking connectivity to $VPS_HOST..."
if ! ssh -q -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -i "$SSH_KEY" "$VPS_HOST" "echo 'Connection established'" >/dev/null 2>&1; then
  echo "ERROR: Cannot connect to $VPS_HOST."
  echo "  - Check if Tailscale is up (if using VPN)"
  echo "  - Check if SSH key is valid: $SSH_KEY"
  echo "  - Check if host is online"
  exit 1
fi
echo "Connectivity confirmed."

RSYNC_RSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no"

# Sync tracked project content while preserving runtime secrets/state on VPS.
rsync -az \
  --exclude ".git/" \
  --exclude ".venv/" \
  --exclude ".env" \
  --exclude "AGENT_RUN_WORKSPACES/" \
  --exclude "artifacts/" \
  --exclude "tmp/" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  -e "$RSYNC_RSH" \
  "$REPO_ROOT/" "$VPS_HOST:$REMOTE_DIR/"

ssh -i "$SSH_KEY" "$VPS_HOST" "
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

  echo '== Python deps =='
  runuser -u ua -- bash -lc 'export PATH=\"/home/ua/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH\"; cd $REMOTE_DIR && uv sync'

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
  chmod 0755 scripts/start_vp_worker.sh scripts/install_vp_worker_services.sh
  APP_ROOT='$REMOTE_DIR' bash scripts/install_vp_worker_services.sh vp.general.primary vp.coder.primary

  echo '== Restart services =='
  systemctl restart \
    universal-agent-gateway \
    universal-agent-api \
    universal-agent-webui \
    universal-agent-telegram \
    universal-agent-vp-worker@vp.general.primary \
    universal-agent-vp-worker@vp.coder.primary
  sleep 4

  echo '== Service status =='
  for s in \
    universal-agent-gateway \
    universal-agent-api \
    universal-agent-webui \
    universal-agent-telegram \
    universal-agent-vp-worker@vp.general.primary \
    universal-agent-vp-worker@vp.coder.primary; do
    printf '%s=' \"\$s\"
    systemctl is-active \"\$s\"
  done

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
  sqlite3 \"\$vp_db\" \"SELECT vp_id, status, worker_id FROM vp_sessions WHERE vp_id IN ('vp.general.primary','vp.coder.primary') ORDER BY vp_id;\" || true
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
