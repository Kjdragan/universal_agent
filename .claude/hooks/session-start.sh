#!/bin/bash
# SessionStart hook — install gh CLI + inject Infisical secrets into the
# web-sandbox environment.
#
# Why: the sandbox boots clean every session, so secrets stored in
# Infisical (e.g. GH_TOKEN) need to be pulled into env at start.  This
# hook does both jobs:
#   1. Install gh CLI if missing (idempotent)
#   2. If Infisical bootstrap creds are present, authenticate to
#      Infisical, fetch the configured secret list, and emit them via
#      $CLAUDE_ENV_FILE so they're available to the rest of the session.
#
# One-time user setup: the three Infisical bootstrap creds must be
# configured as Claude Code on the web sandbox secrets so they reach
# this hook's environment:
#   INFISICAL_CLIENT_ID
#   INFISICAL_CLIENT_SECRET
#   INFISICAL_PROJECT_ID
#
# Add more secrets to inject by appending to INFISICAL_SECRETS_TO_FETCH
# below — one per line, just the secret key name.

set -euo pipefail

# Skip on local desktop sessions — gh + Infisical are already provisioned there.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

# ─── 1. Install gh CLI (idempotent) ────────────────────────────────────────

if command -v gh >/dev/null 2>&1; then
    echo "gh already installed: $(gh --version | head -1)"
else
    echo "Installing GitHub CLI..."
    sudo mkdir -p -m 755 /etc/apt/keyrings
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
    sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq gh
    echo "gh installed: $(gh --version | head -1)"
fi

# ─── 2. Inject Infisical secrets into the session env ──────────────────────

# Secrets to fetch from Infisical production and inject as env vars.
# Add more keys here as needed — each is fetched and exported individually
# so a single missing secret doesn't break the whole hook.
INFISICAL_SECRETS_TO_FETCH=(
    "GH_TOKEN"
)

INFISICAL_API_URL="${INFISICAL_API_URL:-https://app.infisical.com}"
INFISICAL_ENVIRONMENT="${INFISICAL_ENVIRONMENT:-production}"

# Bail if bootstrap creds aren't available — print clear setup instructions.
if [ -z "${INFISICAL_CLIENT_ID:-}" ] || [ -z "${INFISICAL_CLIENT_SECRET:-}" ] || [ -z "${INFISICAL_PROJECT_ID:-}" ]; then
    echo ""
    echo "⚠️  Infisical secret injection skipped — bootstrap creds missing."
    echo ""
    echo "   To enable automatic secret injection in this web sandbox, add"
    echo "   these three secrets to your Claude Code on the web sandbox env"
    echo "   (Settings → Environment Variables / Secrets):"
    echo ""
    echo "     INFISICAL_CLIENT_ID"
    echo "     INFISICAL_CLIENT_SECRET"
    echo "     INFISICAL_PROJECT_ID"
    echo ""
    echo "   Once those are set, the next session will auto-fetch:"
    printf "     - %s\n" "${INFISICAL_SECRETS_TO_FETCH[@]}"
    echo ""
    echo "   gh commands will be unauthenticated this session — rate-limited"
    echo "   against the public API, will fail on private endpoints."
    exit 0
fi

# Authenticate via universal-auth and capture the access token.
echo "🔑 Authenticating to Infisical..."
AUTH_RESPONSE=$(curl -fsSL -X POST "${INFISICAL_API_URL}/api/v1/auth/universal-auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"clientId\":\"${INFISICAL_CLIENT_ID}\",\"clientSecret\":\"${INFISICAL_CLIENT_SECRET}\"}" 2>&1) || {
    echo "❌ Infisical auth failed: $AUTH_RESPONSE"
    exit 0  # don't fail the session — just skip injection
}

# Parse out the accessToken without requiring jq (which may not be installed).
ACCESS_TOKEN=$(echo "$AUTH_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('accessToken',''))" 2>/dev/null || echo "")

if [ -z "$ACCESS_TOKEN" ]; then
    echo "❌ Infisical auth response did not include accessToken."
    exit 0
fi
echo "   ✓ authenticated"

# Fetch each secret and write to $CLAUDE_ENV_FILE so the session sees it.
ENV_FILE="${CLAUDE_ENV_FILE:-/tmp/claude-session-env}"
fetched=0
missing=()
for SECRET_KEY in "${INFISICAL_SECRETS_TO_FETCH[@]}"; do
    URL="${INFISICAL_API_URL}/api/v3/secrets/raw/${SECRET_KEY}?workspaceId=${INFISICAL_PROJECT_ID}&environment=${INFISICAL_ENVIRONMENT}"
    SECRET_RESPONSE=$(curl -fsSL "$URL" -H "Authorization: Bearer ${ACCESS_TOKEN}" 2>/dev/null || echo "")
    if [ -z "$SECRET_RESPONSE" ]; then
        missing+=("$SECRET_KEY")
        continue
    fi
    SECRET_VALUE=$(echo "$SECRET_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('secret',{}).get('secretValue',''))" 2>/dev/null || echo "")
    if [ -z "$SECRET_VALUE" ]; then
        missing+=("$SECRET_KEY")
        continue
    fi
    # Write to $CLAUDE_ENV_FILE — this is how SessionStart hooks export to the session.
    echo "export ${SECRET_KEY}='${SECRET_VALUE}'" >> "$ENV_FILE"
    fetched=$((fetched + 1))
done

echo "   ✓ injected $fetched/${#INFISICAL_SECRETS_TO_FETCH[@]} secret(s) via \$CLAUDE_ENV_FILE"
if [ ${#missing[@]} -gt 0 ]; then
    echo "   ✗ missing in Infisical production: ${missing[*]}"
fi
