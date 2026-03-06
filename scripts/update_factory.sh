#!/usr/bin/env bash
# Update a deployed factory to latest code from a git branch.
#
# Usage:
#   bash scripts/update_factory.sh [--branch BRANCH] [--restart] [--dry-run]
#
# Steps:
#   1. git fetch origin
#   2. git checkout <branch> && git pull --rebase origin <branch>
#   3. uv sync
#   4. If --restart: systemctl --user restart universal-agent-local-factory
#   5. Print new commit hash
#
# Safety:
#   - Does NOT touch .env or .env.bridge (Infisical credentials preserved)
#   - Atomic: if git pull fails, uv sync is skipped
#   - set -e ensures any failure stops the script

set -euo pipefail

# --- Defaults ---
BRANCH="main"
RESTART=false
DRY_RUN=false
FACTORY_DIR="${UA_FACTORY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE_NAME="universal-agent-local-factory"

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --restart)
            RESTART=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown arg: $1"
            echo "Usage: bash scripts/update_factory.sh [--branch BRANCH] [--restart] [--dry-run]"
            exit 1
            ;;
    esac
done

cd "$FACTORY_DIR"

OLD_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "[update] Factory dir: $FACTORY_DIR"
echo "[update] Current commit: $OLD_COMMIT"
echo "[update] Target branch: $BRANCH"

if $DRY_RUN; then
    echo "[update] DRY RUN — would fetch, checkout $BRANCH, pull, uv sync"
    git fetch origin --dry-run 2>&1 || true
    exit 0
fi

# --- Step 1: Fetch ---
echo "[update] Fetching origin..."
git fetch origin

# --- Step 2: Checkout + pull ---
echo "[update] Checking out $BRANCH..."
git checkout "$BRANCH"
git pull --rebase origin "$BRANCH"

# --- Step 3: Install deps ---
echo "[update] Installing dependencies (uv sync)..."
uv sync

NEW_COMMIT=$(git rev-parse --short HEAD)
echo "[update] Updated: $OLD_COMMIT → $NEW_COMMIT"

# --- Step 4: Restart service ---
if $RESTART; then
    echo "[update] Restarting $SERVICE_NAME..."
    if systemctl --user is-active "$SERVICE_NAME" &>/dev/null; then
        systemctl --user restart "$SERVICE_NAME"
        sleep 2
        if systemctl --user is-active "$SERVICE_NAME" &>/dev/null; then
            echo "[update] ✅ $SERVICE_NAME restarted and running"
        else
            echo "[update] ⚠️  $SERVICE_NAME failed to start after restart"
            systemctl --user status "$SERVICE_NAME" --no-pager || true
            exit 1
        fi
    else
        echo "[update] ⚠️  $SERVICE_NAME not currently active, skipping restart"
    fi
fi

echo "[update] Factory updated to $NEW_COMMIT"
