#!/bin/bash

# Ensure we are in the project root
cd "$(dirname "$0")"

echo "🧹 Cleaning up existing processes..."
fuser -k 8001/tcp 2>/dev/null
fuser -k 3000/tcp 2>/dev/null
# Give them a moment to die
sleep 1

echo "🚀 Starting Universal Agent v2.1 Stack..."
echo "---------------------------------------------------"

# 1. Start Backend API (Background)
echo "🔌 Starting API Server (Port 8001)..."
PYTHONPATH=src uv run python -m universal_agent.api.server > api.log 2>&1 &
API_PID=$!
echo "   PID: $API_PID (Logs: tail -f api.log)"

# Wait for API to be ready
echo "⏳ Waiting for API..."
sleep 3

# 2. Start Web UI (Foreground)
echo "💻 Starting Web UI (Port 3000)..."
echo "   Use Ctrl+C to stop both."
echo "---------------------------------------------------"

cd web-ui
npm run dev

# Cleanup on exit
echo ""
echo "🛑 Stopping API Server..."
kill $API_PID 2>/dev/null
echo "✅ Done."
