#!/usr/bin/env bash
# VPS deployment script (local workspace -> remote app dir).
# Usage: ./scripts/deploy_vps.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="${UA_VPS_HOST:-root@187.77.16.29}"
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

RSYNC_RSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no"

# Sync tracked project content while preserving runtime secrets/state on VPS.
rsync -az \
  --delete \
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

  # Keep source/build files owned by service user for build/runtime writes.
  chown -R ua:ua .claude deployment docs OFFICIAL_PROJECT_DOCUMENTATION scripts src tests web-ui webhook_transforms 2>/dev/null || true
  chown ua:ua pyproject.toml uv.lock README.md AGENTS.md .gitignore 2>/dev/null || true

  # Preserve secure env ownership/mode.
  if [ -f .env ]; then
    chown root:ua .env
    chmod 640 .env
  fi

  echo '== Python deps =='
  if [ -x /home/ua/.local/bin/uv ]; then
    runuser -u ua -- bash -lc 'cd $REMOTE_DIR && /home/ua/.local/bin/uv sync'
  elif command -v uv >/dev/null 2>&1; then
    runuser -u ua -- bash -lc 'cd $REMOTE_DIR && uv sync'
  else
    echo 'WARN: uv not found, skipping python dependency sync'
  fi

  echo '== Web UI build =='
  if [ -d web-ui ] && command -v npm >/dev/null 2>&1; then
    runuser -u ua -- bash -lc 'cd $REMOTE_DIR/web-ui && npm install && npm run build'
  else
    echo 'WARN: web-ui or npm missing, skipping web build'
  fi

  echo '== Restart services =='
  systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram
  sleep 4

  echo '== Service status =='
  for s in universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram; do
    printf '%s=' \"\$s\"
    systemctl is-active \"\$s\"
  done

  echo
  echo '== Public health =='
  printf 'API='
  curl -s -o /tmp/deploy_api_health.json -w '%{http_code}' https://api.clearspringcg.com/api/v1/health
  echo
  head -c 220 /tmp/deploy_api_health.json || true
  echo
  printf 'APP='
  curl -s -o /dev/null -w '%{http_code}' https://app.clearspringcg.com/
  echo

  if [ -f .env ]; then
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
  fi
"

echo "Deployment completed."
