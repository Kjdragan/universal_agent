#!/bin/bash
# SessionStart hook — install GitHub CLI for web-sandbox sessions.
#
# Why: the github MCP covers commits/PRs/branches/issues, but workflow-run
# visibility (e.g. `gh run watch` for /ship deploy verification) lives in
# the gh CLI.  The desktop and VPS already have it; web sandboxes spin up
# clean every session, so install it here.
#
# Idempotent: skips install when gh is already on PATH.  Only runs in
# remote (web) sandboxes — desktop sessions already have gh provisioned.

set -euo pipefail

# Skip on local desktop sessions — gh is already installed there.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

# Idempotent: nothing to do if gh already on PATH.
if command -v gh >/dev/null 2>&1; then
    echo "gh already installed: $(gh --version | head -1)"
else
    echo "Installing GitHub CLI..."
    # Use the official apt repo — most reliable for Debian/Ubuntu sandboxes.
    # Mirrors https://github.com/cli/cli/blob/trunk/docs/install_linux.md
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

# Authentication: gh natively reads GH_TOKEN / GITHUB_TOKEN from env, so no
# explicit `gh auth login` step is required.  We just surface the auth
# state so failures are obvious in session logs instead of silently
# breaking the first `gh` command the agent runs.
if [ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]; then
    if gh auth status >/dev/null 2>&1; then
        echo "gh: authenticated via GH_TOKEN/GITHUB_TOKEN."
    else
        echo "gh: GH_TOKEN/GITHUB_TOKEN set but auth check failed (token may be invalid or expired)."
    fi
else
    echo "gh: WARNING — neither GH_TOKEN nor GITHUB_TOKEN is set; gh commands will be unauthenticated and may hit rate limits or fail on private repos."
fi
