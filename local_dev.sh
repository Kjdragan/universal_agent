#!/bin/bash

# Ensure we are in the project root
cd "$(dirname "$0")"

echo "🚀 Starting Agent College (Sidecar) on port 8001..."
# Keep uv cache inside repo to avoid sandbox permission issues
export UV_CACHE_DIR="$(pwd)/.uv-cache"
# Run Agent College in background, redirect logs to file to keep CLI clean
PYTHONPATH=src uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8001 > agent_college.log 2>&1 &
AC_PID=$!

echo "✅ Agent College started (PID: $AC_PID). Logs writing to: agent_college.log"
echo "⏳ Waiting 3 seconds for startup..."
sleep 3

echo "🤖 Starting Universal Agent CLI..."
echo "---------------------------------------------------"
# Run CLI Agent in foreground (Interactive)
PYTHONPATH=src uv run python -m universal_agent.main

# When CLI exits, kill the background process
echo ""
echo "🛑 Stopping Agent College..."
kill $AC_PID
echo "✅ Done."
