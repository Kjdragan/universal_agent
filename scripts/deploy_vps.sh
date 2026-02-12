#!/bin/bash
# VPS Deployment Script
# Usage: ./scripts/deploy_vps.sh

set -e

VPS_HOST="root@187.77.16.29"
SSH_KEY="$HOME/.ssh/id_ed25519"
TARGET_DIR="/opt/universal_agent"

echo "🚀 Deploying to $VPS_HOST..."

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VPS_HOST" "
    set -e
    echo '📂 Navigating to $TARGET_DIR...'
    cd $TARGET_DIR

    echo '📥 Pulling latest changes...'
    git pull

    echo '📦 Syncing dependencies...'
    # Use uv sync if available
    if command -v uv &> /dev/null; then
        uv sync
    else
        echo '⚠️ uv not found, skipping sync (or install uv)'
    fi

    echo '♻️  Restarting services...'
    systemctl restart universal-agent-webui
    systemctl restart universal-agent-gateway

    echo '✅ Verifying services...'
    systemctl is-active universal-agent-webui
    systemctl is-active universal-agent-gateway
"

echo "🎉 Deployment successful!"
