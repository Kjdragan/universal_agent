#!/bin/bash
# VPS Deployment Script
# Usage: ./scripts/deploy_vps.sh

set -e

VPS_HOST="root@187.77.16.29"
SSH_KEY="$HOME/.ssh/id_ed25519"
TARGET_DIR="/opt/universal_agent"

REPO_URL="git@github.com:Kjdragan/universal_agent.git"
BRANCH="dev-telegram"

echo "🚀 Deploying to $VPS_HOST..."

ssh -A -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VPS_HOST" "
    set -e
    set -e

    # Ensure target directory exists
    mkdir -p "$TARGET_DIR"
    cd "$TARGET_DIR"

    # Ensure GitHub is in known_hosts to avoid prompt
    mkdir -p ~/.ssh
    ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null || true

    if [ ! -d ".git" ]; then
        echo "⚠️  Not a git repository. Initializing..."
        git init
        git remote add origin "$REPO_URL"
        git fetch
        # Force checkout to match remote state
        git checkout -t origin/$BRANCH -f || git checkout $BRANCH -f
    else
        echo "📥 Pulling latest changes..."
        # Update remote URL just in case
        git remote set-url origin "$REPO_URL"
        # Fetch and reset hard to ensure clean state matching remote
        git fetch origin
        git reset --hard origin/$BRANCH
    fi

    echo "📦 Syncing dependencies..."
    # Use uv sync if available
    if command -v uv &> /dev/null; then
        uv sync
    else
        echo "⚠️ uv not found, skipping sync (or install uv)"
    fi

    echo "🏗️  Building Web UI..."
    if [ -d "web-ui" ]; then
        cd web-ui
        if command -v npm &> /dev/null; then
            npm install
            npm run build
        else
             echo "⚠️ npm not found, skipping Web UI build"
        fi
        cd ..
    fi

    echo "♻️  Restarting services..."
    systemctl restart universal-agent-webui
    systemctl restart universal-agent-gateway

    echo "✅ Verifying services..."
    systemctl is-active universal-agent-webui
    systemctl is-active universal-agent-gateway
"

echo "🎉 Deployment successful!"
