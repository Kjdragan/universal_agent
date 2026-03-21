#!/usr/bin/env bash
# todoist_mcp_wrapper.sh — Launch todoist-ai MCP server with Infisical secrets.
#
# This wrapper bridges our Infisical-first secret policy with the MCP server's
# env-var requirement. In production (VPS), secrets are injected by the systemd
# service or by Infisical CLI. For local dev, this script fetches from Infisical
# if the env var is not already set.
#
# Usage:
#   ./scripts/todoist_mcp_wrapper.sh
#   # or via .mcp.json:
#   { "command": "./scripts/todoist_mcp_wrapper.sh" }

set -euo pipefail

# If TODOIST_API_KEY is not already in the environment, try Infisical
if [ -z "${TODOIST_API_KEY:-}" ]; then
    if command -v infisical &>/dev/null; then
        export TODOIST_API_KEY
        TODOIST_API_KEY="$(infisical secrets get TODOIST_API_KEY --plain 2>/dev/null || echo '')"
    fi
fi

if [ -z "${TODOIST_API_KEY:-}" ]; then
    echo "ERROR: TODOIST_API_KEY not set and Infisical lookup failed" >&2
    exit 1
fi

# Launch the MCP server
exec npx -y @doist/todoist-ai
