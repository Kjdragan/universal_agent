#!/usr/bin/env bash
# Deploy a LOCAL_WORKER factory on the current machine.
#
# Usage:
#   bash scripts/deploy_local_factory.sh \
#     --infisical-client-id <id> \
#     --infisical-client-secret <secret> \
#     --infisical-project-id <project> \
#     --infisical-environment kevins-desktop
#
# Prerequisites:
#   - git, uv, Python 3.11+
#   - Network access to HQ Redis (Tailscale or public with UFW)
#   - Infisical machine identity for this machine's environment
#
# What this does:
#   1. Clones/updates the repo to ~/universal_agent_factory/
#   2. Runs uv sync to install dependencies
#   3. Creates minimal .env with ONLY Infisical credentials
#   4. Validates Infisical connectivity (secrets load successfully)
#   5. Installs systemd user services (bridge + VP workers)
#   6. Starts the factory
#   7. Validates registration with HQ

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults (overridable via env vars)
# ---------------------------------------------------------------------------
FACTORY_DIR="${UA_FACTORY_DIR:-$HOME/universal_agent_factory}"
REPO_URL="${UA_REPO_URL:-https://github.com/openclaw/universal_agent.git}"
BRANCH="${UA_FACTORY_BRANCH:-main}"
SERVICE_NAME="universal-agent-local-factory"

# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------
INFISICAL_CLIENT_ID=""
INFISICAL_CLIENT_SECRET=""
INFISICAL_PROJECT_ID=""
INFISICAL_ENVIRONMENT=""
SKIP_CLONE=false
SKIP_SERVICE=false

usage() {
    echo "Usage: $0 --infisical-client-id <id> --infisical-client-secret <secret> \\"
    echo "          --infisical-project-id <project> --infisical-environment <env>"
    echo ""
    echo "Options:"
    echo "  --infisical-client-id       Infisical machine identity client ID"
    echo "  --infisical-client-secret    Infisical machine identity client secret"
    echo "  --infisical-project-id       Infisical project ID"
    echo "  --infisical-environment      Infisical environment slug (e.g. kevins-desktop)"
    echo "  --factory-dir <path>         Override factory directory (default: ~/universal_agent_factory)"
    echo "  --branch <branch>            Git branch to checkout (default: main)"
    echo "  --skip-clone                 Skip git clone/pull (use existing code)"
    echo "  --skip-service               Skip systemd service installation"
    echo "  -h, --help                   Show this help"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --infisical-client-id)
            INFISICAL_CLIENT_ID="$2"; shift 2 ;;
        --infisical-client-secret)
            INFISICAL_CLIENT_SECRET="$2"; shift 2 ;;
        --infisical-project-id)
            INFISICAL_PROJECT_ID="$2"; shift 2 ;;
        --infisical-environment)
            INFISICAL_ENVIRONMENT="$2"; shift 2 ;;
        --factory-dir)
            FACTORY_DIR="$2"; shift 2 ;;
        --branch)
            BRANCH="$2"; shift 2 ;;
        --skip-clone)
            SKIP_CLONE=true; shift ;;
        --skip-service)
            SKIP_SERVICE=true; shift ;;
        -h|--help)
            usage ;;
        *)
            echo "Unknown option: $1"; usage ;;
    esac
done

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
if [[ -z "$INFISICAL_CLIENT_ID" || -z "$INFISICAL_CLIENT_SECRET" || \
      -z "$INFISICAL_PROJECT_ID" || -z "$INFISICAL_ENVIRONMENT" ]]; then
    echo "ERROR: All --infisical-* arguments are required."
    echo ""
    usage
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== Universal Agent Local Factory Deployment ==="
log "Factory dir:  $FACTORY_DIR"
log "Environment:  $INFISICAL_ENVIRONMENT"
log "Branch:       $BRANCH"

# ---------------------------------------------------------------------------
# Step 1: Clone or update repo
# ---------------------------------------------------------------------------
if [[ "$SKIP_CLONE" == "false" ]]; then
    if [[ -d "$FACTORY_DIR/.git" ]]; then
        log "Step 1: Updating existing repo..."
        cd "$FACTORY_DIR"
        git fetch origin
        git checkout "$BRANCH"
        git pull origin "$BRANCH"
    else
        log "Step 1: Cloning repo..."
        git clone --branch "$BRANCH" "$REPO_URL" "$FACTORY_DIR"
    fi
else
    log "Step 1: Skipping clone (--skip-clone)"
fi

cd "$FACTORY_DIR"

# ---------------------------------------------------------------------------
# Step 2: Install dependencies via uv
# ---------------------------------------------------------------------------
log "Step 2: Installing dependencies..."
if command -v uv &>/dev/null; then
    uv sync
else
    echo "ERROR: uv not found. Install via: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3: Create minimal .env with Infisical credentials only
# ---------------------------------------------------------------------------
log "Step 3: Writing minimal .env (Infisical credentials only)..."
ENV_FILE="$FACTORY_DIR/.env"

cat > "$ENV_FILE" <<EOF
# Universal Agent Local Factory — Infisical Bootstrap
# Generated by deploy_local_factory.sh on $(date -Iseconds)
# All runtime parameters are loaded from Infisical at startup.
# This file contains ONLY the credentials needed to authenticate.
INFISICAL_CLIENT_ID=${INFISICAL_CLIENT_ID}
INFISICAL_CLIENT_SECRET=${INFISICAL_CLIENT_SECRET}
INFISICAL_PROJECT_ID=${INFISICAL_PROJECT_ID}
INFISICAL_ENVIRONMENT=${INFISICAL_ENVIRONMENT}
EOF

chmod 600 "$ENV_FILE"
log "  .env written to $ENV_FILE (mode 600)"

# ---------------------------------------------------------------------------
# Step 4: Validate Infisical connectivity
# ---------------------------------------------------------------------------
log "Step 4: Validating Infisical connectivity..."
VALIDATE_CMD='
import os, sys
os.environ["INFISICAL_CLIENT_ID"] = "'"$INFISICAL_CLIENT_ID"'"
os.environ["INFISICAL_CLIENT_SECRET"] = "'"$INFISICAL_CLIENT_SECRET"'"
os.environ["INFISICAL_PROJECT_ID"] = "'"$INFISICAL_PROJECT_ID"'"
os.environ["INFISICAL_ENVIRONMENT"] = "'"$INFISICAL_ENVIRONMENT"'"
try:
    from universal_agent.infisical_loader import load_infisical_into_env
    count = load_infisical_into_env()
    print(f"OK: Loaded {count} secrets from Infisical ({os.environ.get(\"INFISICAL_ENVIRONMENT\", \"?\")})")
except Exception as e:
    print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
'

if PYTHONPATH="$FACTORY_DIR/src" "$FACTORY_DIR/.venv/bin/python" -c "$VALIDATE_CMD"; then
    log "  Infisical validation passed."
else
    log "ERROR: Infisical validation failed. Check credentials."
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 4b: Render local web-ui env from Infisical (optional, non-fatal)
# ---------------------------------------------------------------------------
if [[ -x "$FACTORY_DIR/scripts/install_local_webui_env.sh" ]]; then
    log "Step 4b: Rendering web-ui/.env.local from Infisical..."
    if APP_ROOT="$FACTORY_DIR" DEPLOY_PROFILE="local_workstation" \
        "$FACTORY_DIR/scripts/install_local_webui_env.sh"; then
        log "  web-ui env render succeeded."
    else
        log "  WARNING: web-ui env render failed. You can retry manually with:"
        log "    APP_ROOT=$FACTORY_DIR DEPLOY_PROFILE=local_workstation $FACTORY_DIR/scripts/install_local_webui_env.sh"
    fi
else
    log "Step 4b: Skipping web-ui env render (installer script missing)."
fi

# ---------------------------------------------------------------------------
# Step 5: Install systemd user service
# ---------------------------------------------------------------------------
if [[ "$SKIP_SERVICE" == "false" ]]; then
    log "Step 5: Installing systemd user service..."
    SERVICE_SRC="$FACTORY_DIR/deployment/systemd-user/${SERVICE_NAME}.service"

    if [[ ! -f "$SERVICE_SRC" ]]; then
        log "  WARNING: Service file not found at $SERVICE_SRC"
        log "  Skipping systemd installation."
    else
        mkdir -p "$HOME/.config/systemd/user"

        # Replace __REPO_ROOT__ placeholder with actual factory dir
        sed "s|__REPO_ROOT__|$FACTORY_DIR|g" "$SERVICE_SRC" \
            > "$HOME/.config/systemd/user/${SERVICE_NAME}.service"

        systemctl --user daemon-reload
        systemctl --user enable "$SERVICE_NAME"
        log "  Service installed and enabled."
    fi
else
    log "Step 5: Skipping service installation (--skip-service)"
fi

# ---------------------------------------------------------------------------
# Step 6: Start the factory
# ---------------------------------------------------------------------------
if [[ "$SKIP_SERVICE" == "false" ]]; then
    log "Step 6: Starting factory service..."
    systemctl --user restart "$SERVICE_NAME" || true
    sleep 2
    if systemctl --user is-active --quiet "$SERVICE_NAME"; then
        log "  Factory service is running."
    else
        log "  WARNING: Service may not have started. Check: journalctl --user -u $SERVICE_NAME"
    fi
else
    log "Step 6: Skipping service start (--skip-service)"
    log "  To run manually:"
    log "    cd $FACTORY_DIR"
    log "    source .env"
    log "    PYTHONPATH=src .venv/bin/python -m universal_agent.delegation.bridge_main"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log ""
log "=== Deployment Complete ==="
log "Factory dir:     $FACTORY_DIR"
log "Environment:     $INFISICAL_ENVIRONMENT"
log "Service:         $SERVICE_NAME"
log ""
log "Useful commands:"
log "  systemctl --user status $SERVICE_NAME"
log "  journalctl --user -u $SERVICE_NAME -f"
log "  systemctl --user restart $SERVICE_NAME"
log ""
