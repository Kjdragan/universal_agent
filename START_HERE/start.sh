#!/bin/bash
set -e

# ==========================================
# PERMISSION FIX FOR RAILWAY VOLUMES
# ==========================================
# Railway mounts /app/data as root. We must give ownership to appuser.
echo "🛠️  Fixing permissions for /app/data..."
chown -R appuser:appuser /app/data
mkdir -p /app/AGENT_RUN_WORKSPACES
chown -R appuser:appuser /app/AGENT_RUN_WORKSPACES

# ==========================================
# START SERVICES AS APPUSER
# ==========================================

# DIAGNOSTICS: Check Network Connectivity (Run as root before dropping privileges or as appuser?)
# We'll run as root to be sure, then appuser implies network is shared.
echo "🔍 DIAGNOSTICS: Testing Network Connectivity..."
echo "1. Pinging Google (DNS Check)..."
curl -I https://google.com || echo "❌ Failed to reach Google"

echo "2. Testing/Timing Telegram API (Reachability Check)..."
# -v for verbose to see handshake, -m 10 to timeout in 10s
curl -v -m 10 https://api.telegram.org || echo "❌ Failed to reach Telegram API"

# Start Agent College (LogfireFetch) in the background
echo "🎓 Starting Agent College Service..."
su -s /bin/bash appuser -c "uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8000 --host 127.0.0.1" &

# Start Telegram Bot (Main Process)
# This will handle the $PORT binding for Webhooks if configured
echo "🤖 Starting Universal Agent Telegram Bot..."
export PYTHONPATH=$PYTHONPATH:$(pwd)/src

# Ensure PORT is set and treated as a number
SERVER_PORT=${PORT:-8000}
echo "DEBUG: Using Port: '$SERVER_PORT'"
echo "DEBUG: Current Directory: $(pwd)"
echo "DEBUG: PYTHONPATH: $PYTHONPATH"

# Force unbuffered output to see logs immediately
export PYTHONUNBUFFERED=1

# Run with debug logging as appuser
# exec acts on the last command to take over PID 1 (or close to it)
exec su -s /bin/bash appuser -c "exec uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port $SERVER_PORT --log-level debug"
