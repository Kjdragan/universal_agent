#!/bin/bash

# Ensure we are in the project root
cd "$(dirname "$0")"

echo "🚀 Starting Agent College (Sidecar) on port 8001..."
# Keep uv cache inside repo to avoid sandbox permission issues
export UV_CACHE_DIR="$(pwd)/.uv-cache"
# Set higher token limit for batch reading (50k words)
export UA_BATCH_MAX_WORDS=50000

# Run Agent College in background
PYTHONPATH=src uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8001 > agent_college.log 2>&1 &
AC_PID=$!

echo "✅ Agent College started (PID: $AC_PID)."
echo "⏳ Waiting 2 seconds for startup..."
sleep 2

echo "🤖 Starting Universal Agent Web UI..."
echo "---------------------------------------------------"
echo "🌐 UI URL:  http://localhost:8000"
echo "---------------------------------------------------"
echo "Press Ctrl+C to stop all services."

# Run Server in foreground
PYTHONPATH=src uv run src/universal_agent/server.py

# Cleanup on exit
echo ""
echo "🛑 Stopping Agent College..."
kill $AC_PID 2>/dev/null
echo "✅ Done."
