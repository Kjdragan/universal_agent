#!/bin/bash
#
# Gateway Mode - Production-Like Stack
#
# Runs the canonical execution engine via the Gateway Server.
# Both CLI and Web UI connect to the same gateway for unified execution.
#
# Architecture:
#   Gateway Server (8002) <-- CLI client (terminal)
#                         <-- Web UI (api.server:8001 + frontend:3000)
#
# Usage:
#   ./start_gateway.sh              # Gateway + Web UI
#   ./start_gateway.sh --server     # Gateway server only (for CLI testing)
#   ./start_gateway.sh --ui         # Web UI only (assumes gateway already running)
#
# CLI client (separate terminal):
#   UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh
#

cd "$(dirname "$0")"

# Keep uv cache inside repo
export UV_CACHE_DIR="$(pwd)/.uv-cache"

# Gateway URL for all clients
export UA_GATEWAY_URL="http://localhost:8002"

MODE="${1:-full}"

cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    [ -n "$GATEWAY_PID" ] && kill $GATEWAY_PID 2>/dev/null
    [ -n "$API_PID" ] && kill $API_PID 2>/dev/null
    fuser -k 8002/tcp 2>/dev/null
    fuser -k 8001/tcp 2>/dev/null
    fuser -k 3000/tcp 2>/dev/null
    echo "✅ Done."
    exit 0
}
trap cleanup SIGINT SIGTERM

case "$MODE" in
    --server)
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║         GATEWAY SERVER ONLY                                  ║"
        echo "╠══════════════════════════════════════════════════════════════╣"
        echo "║  Gateway:   http://localhost:8002                            ║"
        echo "║                                                              ║"
        echo "║  CLI client (separate terminal):                             ║"
        echo "║    UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh   ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        echo ""
        PYTHONPATH=src uv run python -m universal_agent.gateway_server
        ;;

    --ui)
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║         WEB UI ONLY (expects gateway on 8002)                ║"
        echo "╠══════════════════════════════════════════════════════════════╣"
        echo "║  Gateway:   $UA_GATEWAY_URL (must be running)                ║"
        echo "║  API:       http://localhost:8001                            ║"
        echo "║  Web UI:    http://localhost:3000                            ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        echo ""
        
        # Check if gateway is running
        if ! curl -s http://localhost:8002/api/v1/health > /dev/null 2>&1; then
            echo "❌ Gateway server not running on port 8002"
            echo "   Start it first with: ./start_gateway.sh --server"
            exit 1
        fi
        echo "✅ Gateway server detected on port 8002"
        
        # Clean up existing processes
        fuser -k 8001/tcp 2>/dev/null
        fuser -k 3000/tcp 2>/dev/null
        sleep 1
        
        # Start API server (connects to gateway)
        echo "🔌 Starting API Server (Port 8001)..."
        UA_GATEWAY_URL=http://localhost:8002 PYTHONPATH=src uv run python -m universal_agent.api.server > api.log 2>&1 &
        API_PID=$!
        echo "   PID: $API_PID (Logs: tail -f api.log)"
        sleep 2
        
        # Start Web UI
        echo "💻 Starting Web UI (Port 3000)..."
        cd web-ui
        npm run dev
        ;;

    full|*)
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║         GATEWAY MODE - FULL STACK                            ║"
        echo "╠══════════════════════════════════════════════════════════════╣"
        echo "║  Gateway:   http://localhost:8002                            ║"
        echo "║  API:       http://localhost:8001                            ║"
        echo "║  Web UI:    http://localhost:3000                            ║"
        echo "║                                                              ║"
        echo "║  CLI client (separate terminal):                             ║"
        echo "║    UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh   ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        echo ""
        
        # Clean up existing processes
        echo "🧹 Cleaning up existing processes..."
        fuser -k 8002/tcp 2>/dev/null
        fuser -k 8001/tcp 2>/dev/null
        fuser -k 3000/tcp 2>/dev/null
        sleep 1
        
        # 1. Start Gateway Server (background)
        echo "🚀 Starting Gateway Server (Port 8002)..."
        PYTHONPATH=src uv run python -m universal_agent.gateway_server > gateway.log 2>&1 &
        GATEWAY_PID=$!
        echo "   PID: $GATEWAY_PID (Logs: tail -f gateway.log)"
        
        # Wait for gateway to be ready
        echo "⏳ Waiting for Gateway..."
        for i in {1..30}; do
            if curl -s http://localhost:8002/api/v1/health > /dev/null 2>&1; then
                echo "   ✅ Gateway ready"
                break
            fi
            sleep 1
            if [ $i -eq 30 ]; then
                echo "   ❌ Gateway failed to start. Check gateway.log"
                cat gateway.log | tail -20
                exit 1
            fi
        done
        
        # 2. Start API Server (background, connects to gateway)
        echo "🔌 Starting API Server (Port 8001)..."
        UA_GATEWAY_URL=http://localhost:8002 PYTHONPATH=src uv run python -m universal_agent.api.server > api.log 2>&1 &
        API_PID=$!
        echo "   PID: $API_PID (Logs: tail -f api.log)"
        sleep 2
        
        # 3. Start Web UI (foreground)
        echo "💻 Starting Web UI (Port 3000)..."
        echo "---------------------------------------------------"
        echo "   Use Ctrl+C to stop all services."
        echo ""
        cd web-ui
        npm run dev
        ;;
esac
