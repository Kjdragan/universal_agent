#!/usr/bin/env bash
#
# claude_with_mcp_env.sh — launch `claude` with secrets injected from
# Infisical so that `${VAR}` placeholders in `.mcp.json` resolve and
# the MCP child processes start correctly.
#
# Why this exists
# ---------------
# `.mcp.json` declares the MCP servers Claude Code spawns. Two of them
# need real credentials at process-start:
#
#     AgentMail (env.AGENTMAIL_API_KEY = "${AGENTMAIL_API_KEY}")
#     discord   (env.DISCORD_TOKEN     = "${DISCORD_BOT_TOKEN}")
#
# Claude Code substitutes `${VAR}` from the env of the parent process
# that launched `claude`. UA's canonical secrets provider is Infisical
# (NOT `.env` files — see CLAUDE.md). UA services run with secrets
# already injected because they call `initialize_runtime_secrets()` at
# startup. But an interactive `claude` invocation from a fresh shell
# does NOT run that bootstrap, so `${AGENTMAIL_API_KEY}` and
# `${DISCORD_BOT_TOKEN}` substitute to empty and the MCP children
# fail. That is what Claude Code Doctor surfaces as "MCP server needs
# <token>" — it does NOT mean the token must be inlined into
# `.mcp.json` (that would leak secrets into git).
#
# This launcher solves it the canonical way: `infisical run --env=…
# -- claude`. Infisical resolves the project's secrets, exports them
# as env vars on the wrapped process, and `claude` inherits them.
#
# Usage
# -----
#   ./scripts/claude_with_mcp_env.sh [claude args…]
#
# Environment overrides
# ---------------------
#   INFISICAL_ENVIRONMENT  Which Infisical env to pull from. Defaults
#                          to "prod" on the VPS, but respects whatever
#                          is already exported (e.g. "dev" on a
#                          desktop dev tree). Override per-invocation
#                          with `INFISICAL_ENVIRONMENT=dev ./scripts/...`.
#   INFISICAL_PROJECT_ID   The project to pull from. Required by
#                          `infisical run`. Normally already exported
#                          by the operator's shell rc on the VPS; if
#                          unset we read it from `.infisical.json`.
#
# Idiom mirrored from `scripts/dev_up.sh:160-208` which already wraps
# UA service launches with `infisical run`.

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFISICAL_ENV="${INFISICAL_ENVIRONMENT:-prod}"

# Resolve project ID: prefer an exported env var, fall back to the
# workspaceId in .infisical.json (which `infisical run` would also
# auto-discover, but being explicit keeps the launcher debuggable).
if [ -z "${INFISICAL_PROJECT_ID:-}" ]; then
    if [ -f "$REPO_ROOT/.infisical.json" ]; then
        INFISICAL_PROJECT_ID="$(python3 -c "import json,sys; print(json.load(open('$REPO_ROOT/.infisical.json')).get('workspaceId',''))" 2>/dev/null || echo "")"
    fi
fi

if [ -z "${INFISICAL_PROJECT_ID:-}" ]; then
    echo "❌ INFISICAL_PROJECT_ID is unset and no workspaceId found in .infisical.json." >&2
    echo "   Either export INFISICAL_PROJECT_ID or run from a checkout with .infisical.json." >&2
    exit 1
fi

if ! command -v infisical >/dev/null 2>&1; then
    echo "❌ infisical CLI not on PATH. Install per docs/03_Operations/97_Infisical_CLI_Reference_And_Lessons_Learned_*.md" >&2
    exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "❌ claude CLI not on PATH." >&2
    exit 1
fi

echo "🔐 Injecting Infisical secrets (env=$INFISICAL_ENV) and launching claude…" >&2

cd "$REPO_ROOT"
exec infisical run \
    --env="$INFISICAL_ENV" \
    --projectId="$INFISICAL_PROJECT_ID" \
    -- claude "$@"
