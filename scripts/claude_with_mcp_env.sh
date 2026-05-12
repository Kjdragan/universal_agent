#!/usr/bin/env bash
#
# claude_with_mcp_env.sh — launch `claude` with Infisical-bootstrapped
# secrets so that `${VAR}` placeholders in `.mcp.json` resolve and the
# MCP child processes start correctly.
#
# Why this exists
# ---------------
# `.mcp.json` declares MCP servers Claude Code spawns. Some need real
# credentials at process start, sourced via `${VAR}` substitution from
# the parent process env (e.g. AGENTMAIL_API_KEY, DISCORD_BOT_TOKEN,
# HOSTINGER_API_TOKEN).
#
# UA's canonical secrets provider is Infisical (see CLAUDE.md). UA
# services get those vars because they call
# `initialize_runtime_secrets()` at startup, which reads the
# machine-identity bootstrap creds from /opt/universal_agent/.env
# (INFISICAL_CLIENT_ID/SECRET/PROJECT_ID/ENVIRONMENT), uses the Python
# Infisical SDK, and injects every project secret onto `os.environ`.
#
# An interactive `claude` invocation does NOT run that bootstrap, so
# the placeholders in `.mcp.json` substitute to empty and MCP children
# fail. Claude Code Doctor surfaces this as "MCP server needs <token>"
# — the right fix is to run the same Python bootstrap UA services use
# and exec `claude` from inside that bootstrapped process. That is
# what this launcher does (via `scripts/_claude_launcher.py`).
#
# Why not `infisical run` (the CLI)
# ---------------------------------
# It's a separate auth context that requires either an interactive
# `infisical login` session (~/.infisical/) or independent parsing of
# machine-identity env vars. On the VPS we have machine-identity creds
# in /opt/universal_agent/.env but no interactive CLI login, so
# `infisical run` from a fresh shell triggers an interactive login
# prompt and fails non-tty. The Python SDK path used by
# `initialize_runtime_secrets()` is the canonical UA auth path and
# works headless.
#
# Usage
# -----
#   ./scripts/claude_with_mcp_env.sh [claude args…]
#
# Environment
# -----------
#   UA_INSTALL_ROOT    Production UA checkout (default
#                      /opt/universal_agent). The launcher uses its
#                      .env (bootstrap creds) and its uv venv (which
#                      has infisicalsdk installed).

set -e

# Resolve UA_INSTALL_ROOT with auto-detection (2026-05-11):
#   1. Honor explicit UA_INSTALL_ROOT if set
#   2. Otherwise try /opt/universal_agent (canonical VPS prod path)
#   3. Otherwise fall back to the repo containing this script (desktop dev case)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT_FROM_SCRIPT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "${UA_INSTALL_ROOT:-}" ]; then
    if [ -f "/opt/universal_agent/.env" ]; then
        UA_INSTALL_ROOT="/opt/universal_agent"
    elif [ -f "$REPO_ROOT_FROM_SCRIPT/.env" ]; then
        UA_INSTALL_ROOT="$REPO_ROOT_FROM_SCRIPT"
    else
        UA_INSTALL_ROOT="/opt/universal_agent"   # default for error msg below
    fi
fi

LAUNCHER="$SCRIPT_DIR/_claude_launcher.py"

if [ ! -f "$UA_INSTALL_ROOT/.env" ]; then
    echo "❌ $UA_INSTALL_ROOT/.env not found." >&2
    echo "   Auto-detection tried /opt/universal_agent/.env and $REPO_ROOT_FROM_SCRIPT/.env." >&2
    echo "   Set UA_INSTALL_ROOT explicitly to a checkout with Infisical bootstrap creds:" >&2
    echo "     export UA_INSTALL_ROOT=/path/to/universal_agent" >&2
    echo "   Or run scripts/bootstrap_local_hq_dev.sh first to create the .env." >&2
    exit 1
fi

if [ ! -r "$UA_INSTALL_ROOT/.env" ]; then
    echo "❌ $UA_INSTALL_ROOT/.env is not readable by user $(id -un)." >&2
    echo "   Production .env is mode 0600; either run as the owning user or copy creds." >&2
    exit 1
fi

if [ ! -f "$LAUNCHER" ]; then
    echo "❌ Launcher helper $LAUNCHER not found." >&2
    exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "❌ claude CLI not on PATH." >&2
    exit 1
fi

# Hand off to the python launcher inside the prod uv venv (has
# infisicalsdk + UA's deps). The launcher sources .env, calls
# initialize_runtime_secrets(), and execs claude with the bootstrapped
# env intact. Running from $UA_INSTALL_ROOT so `uv run` finds the
# right pyproject.toml / venv.
cd "$UA_INSTALL_ROOT"

# Auto-inject --dangerously-skip-permissions when the user is launching
# an interactive coding session. Skip it for the management subcommands
# (claude agents, claude auth, claude doctor, etc.) — those don't accept
# the flag and would reject it.
#
# Management subcommands enumerated from `claude --help` (v2.1.139+):
#   agents, auth, auto-mode, doctor, install, mcp, plugin(s), project,
#   setup-token, ultrareview, update/upgrade.
# Anything else (no args, prompt strings, --print, --bg, --resume, etc.)
# is treated as an interactive session that should bypass permission prompts.
_first_arg="${1:-}"
case "$_first_arg" in
    agents|auth|auto-mode|doctor|install|mcp|plugin|plugins|project|setup-token|ultrareview|update|upgrade)
        exec uv run --quiet python "$LAUNCHER" "$@"
        ;;
    *)
        exec uv run --quiet python "$LAUNCHER" --dangerously-skip-permissions "$@"
        ;;
esac
