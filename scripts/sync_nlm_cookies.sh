#!/usr/bin/env bash
# Sync NLM cookies from local machine to VPS.
#
# Usage:
#   scripts/sync_nlm_cookies.sh                  # sync + verify
#   scripts/sync_nlm_cookies.sh --verify-only    # just check VPS auth
#
# Prerequisites:
#   - Valid NLM auth on local machine (run `nlm login` if expired)
#   - SSH access to hostinger-vps (via Tailscale)
#
# This replaces the Infisical NOTEBOOKLM_AUTH_COOKIE_HEADER approach,
# which goes stale. Instead, sync fresh cookies on-demand from the
# local machine where Chrome-based login keeps them fresh.

set -euo pipefail

VPS_HOST="${UA_VPS_HOST:-hostinger-vps}"
VPS_USER="${UA_VPS_USER:-root}"
NLM_PROFILE="${UA_NOTEBOOKLM_PROFILE:-vps}"
LOCAL_PROFILE_DIR="$HOME/.notebooklm-mcp-cli/profiles/$NLM_PROFILE"
REMOTE_USER="ua"
REMOTE_PROFILE_DIR="/home/$REMOTE_USER/.notebooklm-mcp-cli/profiles/$NLM_PROFILE"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

verify_only=false
if [[ "${1:-}" == "--verify-only" ]]; then
    verify_only=true
fi

# Step 1: Verify local auth is valid
echo -e "${YELLOW}[1/4] Checking local NLM auth...${NC}"
local_check=$(nlm login --check --profile "$NLM_PROFILE" 2>&1 || true)
if ! echo "$local_check" | grep -q "Authentication valid"; then
    echo -e "${RED}✗ Local auth is invalid. Run: nlm login --profile $NLM_PROFILE${NC}"
    echo "$local_check"
    exit 1
fi
echo -e "${GREEN}✓ Local auth valid${NC}"

if $verify_only; then
    echo -e "${YELLOW}[2/4] Checking VPS NLM auth...${NC}"
    ssh "$VPS_HOST" "export PATH=/home/$REMOTE_USER/.local/bin:\$PATH && nlm login --check --profile $NLM_PROFILE 2>&1" || true
    exit 0
fi

# Step 2: Sync cookies to VPS
echo -e "${YELLOW}[2/4] Syncing cookies to VPS ($VPS_HOST)...${NC}"
if [[ ! -f "$LOCAL_PROFILE_DIR/cookies.json" ]]; then
    echo -e "${RED}✗ No local cookies.json at $LOCAL_PROFILE_DIR${NC}"
    exit 1
fi

ssh "$VPS_HOST" "mkdir -p $REMOTE_PROFILE_DIR"
scp -q "$LOCAL_PROFILE_DIR/cookies.json" "$VPS_HOST:$REMOTE_PROFILE_DIR/cookies.json"
scp -q "$LOCAL_PROFILE_DIR/metadata.json" "$VPS_HOST:$REMOTE_PROFILE_DIR/metadata.json" 2>/dev/null || true

# Fix ownership (files were copied as root, need to be owned by ua)
ssh "$VPS_HOST" "chown -R $REMOTE_USER:$REMOTE_USER /home/$REMOTE_USER/.notebooklm-mcp-cli/"
echo -e "${GREEN}✓ Cookies synced${NC}"

# Step 3: Verify on VPS via CLI
echo -e "${YELLOW}[3/4] Verifying NLM auth on VPS...${NC}"
vps_check=$(ssh "$VPS_HOST" "su - $REMOTE_USER -c 'export PATH=/home/$REMOTE_USER/.local/bin:\$PATH && nlm login --check --profile $NLM_PROFILE 2>&1'" 2>&1 || true)
echo "$vps_check"
if echo "$vps_check" | grep -q "Authentication valid"; then
    echo -e "${GREEN}✓ VPS auth verified${NC}"
else
    echo -e "${YELLOW}⚠ CLI check inconclusive (MCP server may still work)${NC}"
fi

# Step 4: Restart NLM MCP server to pick up new cookies
echo -e "${YELLOW}[4/4] NLM MCP server will pick up new cookies on next refresh_auth call${NC}"
echo -e "${GREEN}✓ Done! NLM cookies synced to VPS.${NC}"
echo ""
echo "Next: Start a new Simone session and try the NotebookLM prompt."
