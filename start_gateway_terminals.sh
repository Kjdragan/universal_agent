#!/bin/bash
#
# Gateway Mode - Multi-Terminal Launcher
#
# Launches Gateway, API, and Web UI in separate gnome-terminal windows.
#

cd "$(dirname "$0")"

# Keep uv cache inside repo
export UV_CACHE_DIR="$(pwd)/.uv-cache"
export PYTHONPATH=src
export UA_GATEWAY_URL="http://localhost:8002"

echo "🧹 Cleaning up existing processes..."
fuser -k 8002/tcp 2>/dev/null
fuser -k 8001/tcp 2>/dev/null
fuser -k 3000/tcp 2>/dev/null
sleep 1

echo "🚀 Launching Gateway Server in a new terminal..."
gnome-terminal --title="UA-GATEWAY (8002)" -- bash -c "PYTHONPATH=src uv run python -m universal_agent.gateway_server; exec bash"

# Wait for gateway to be ready
echo "⏳ Waiting for Gateway health check..."
for i in {1..30}; do
    if curl -s http://localhost:8002/api/v1/health > /dev/null 2>&1; then
        echo "   ✅ Gateway ready"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        echo "   ❌ Gateway failed to start or health check timed out."
        exit 1
    fi
done

echo "🔌 Launching API Server in a new terminal..."
gnome-terminal --title="UA-API (8001)" -- bash -c "UA_GATEWAY_URL=http://localhost:8002 PYTHONPATH=src uv run python -m universal_agent.api.server; exec bash"

echo "💻 Launching Web UI in a new terminal..."
gnome-terminal --title="UA-WEB-UI (3000)" -- bash -c "cd web-ui && BROWSER=none npm run dev; exec bash"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         MULTI-TERMINAL STACK STARTED                         ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Gateway (8002): Dedicated Terminal                          ║"
echo "║  API (8001):     Dedicated Terminal                          ║"
echo "║  Web UI (3000):  Dedicated Terminal                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo "Check the new terminal windows for logs."
