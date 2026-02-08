#!/bin/bash
#
# Gateway Mode - Production-Like Stack
#
# Runs the canonical execution engine via the Gateway Server.
# Both CLI and Web UI connect to the same gateway for unified execution.
#
# Architecture:
#   Gateway Server (UA_GATEWAY_PORT) <-- CLI client (terminal)
#                                   <-- Web UI (api.server:8001 + frontend:3000)
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

# Load repo .env if present so subprocesses (e.g., Claude Code CLI) inherit settings.
# This avoids surprising defaults like the 8192 output token cap.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Keep uv cache inside repo
export UV_CACHE_DIR="$(pwd)/.uv-cache"

# Composio cache directory (must be writable when running as appuser)
if [ -z "$COMPOSIO_CACHE_DIR" ]; then
    if [ -d /app ] && [ -w /app ]; then
        export COMPOSIO_CACHE_DIR="/app/data/.composio"
    else
        export COMPOSIO_CACHE_DIR="$(pwd)/.cache/composio"
    fi
fi
mkdir -p "$COMPOSIO_CACHE_DIR" 2>/dev/null || true
chown -R appuser:appuser "$COMPOSIO_CACHE_DIR" 2>/dev/null || true

# Ensure appuser has a writable HOME and XDG dirs for CLI caches/config
if [ -d /app ] && [ -w /app ]; then
    export HOME="/app"
    export XDG_CACHE_HOME="/app/.cache"
    export XDG_CONFIG_HOME="/app/.config"
else
    export HOME="${HOME:-$(pwd)}"
    export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
    export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
fi
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" 2>/dev/null || true
chown -R appuser:appuser "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" 2>/dev/null || true

# Gateway bind settings
# In Railway (or other PaaS), use PORT if provided so the public URL routes correctly.
if [ -n "$PORT" ]; then
    export UA_GATEWAY_PORT="$PORT"
    export UA_GATEWAY_HOST="${UA_GATEWAY_HOST:-0.0.0.0}"
else
    export UA_GATEWAY_PORT="${UA_GATEWAY_PORT:-8002}"
    export UA_GATEWAY_HOST="${UA_GATEWAY_HOST:-0.0.0.0}"
fi

# Gateway URL for local clients (do not override if already set)
if [ -z "$UA_GATEWAY_URL" ]; then
    export UA_GATEWAY_URL="http://localhost:${UA_GATEWAY_PORT}"
fi

MODE="${1:-full}"

run_gateway_foreground() {
    if [ "$(id -u)" -eq 0 ] && id -u appuser >/dev/null 2>&1; then
        echo "👤 Running gateway as appuser (via su)"
        su -m -s /bin/bash appuser -c "PYTHONPATH=src uv run python -m universal_agent.gateway_server"
    else
        echo "👤 Running gateway as $(id -un)"
        PYTHONPATH=src uv run python -m universal_agent.gateway_server
    fi
}

run_gateway_background() {
    if [ "$(id -u)" -eq 0 ] && id -u appuser >/dev/null 2>&1; then
        echo "👤 Running gateway as appuser (via su)"
        su -m -s /bin/bash appuser -c "PYTHONPATH=src uv run python -m universal_agent.gateway_server" > gateway.log 2>&1 &
    else
        echo "👤 Running gateway as $(id -un)"
        PYTHONPATH=src uv run python -m universal_agent.gateway_server > gateway.log 2>&1 &
    fi
}

cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    [ -n "$GATEWAY_PID" ] && kill $GATEWAY_PID 2>/dev/null
    [ -n "$API_PID" ] && kill $API_PID 2>/dev/null
    fuser -k "${UA_GATEWAY_PORT}"/tcp 2>/dev/null
    fuser -k 8001/tcp 2>/dev/null
    fuser -k 3000/tcp 2>/dev/null
    echo "✅ Done."
    exit 0
}
trap cleanup SIGINT SIGTERM

clean_runtime_state() {
    echo "🧼 Cleaning runtime state..."

    # Stop any existing stack first
    fuser -k "${UA_GATEWAY_PORT}"/tcp 2>/dev/null || true
    fuser -k 8001/tcp 2>/dev/null || true
    fuser -k 3000/tcp 2>/dev/null || true
    pkill -f "universal_agent.gateway_server" 2>/dev/null || true
    pkill -f "python -m universal_agent.api.server" 2>/dev/null || true
    pkill -f "next dev" 2>/dev/null || true
    pkill -f "npm run dev" 2>/dev/null || true
    sleep 1

    # Archive existing workspace runtime/session state
    local ws_dir="AGENT_RUN_WORKSPACES"
    local archive_root="AGENT_RUN_WORKSPACES_ARCHIVE"
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local archive_dir="${archive_root}/reset_${ts}"

    mkdir -p "${ws_dir}" "${archive_dir}"

    shopt -s nullglob
    local moved=0
    for p in "${ws_dir}"/session_* "${ws_dir}"/tg_* "${ws_dir}"/api_*; do
        mv "${p}" "${archive_dir}/"
        moved=1
    done
    for f in \
        "${ws_dir}/approvals.json" \
        "${ws_dir}/runtime_state.db" \
        "${ws_dir}/runtime_state.db-shm" \
        "${ws_dir}/runtime_state.db-wal"; do
        if [ -e "${f}" ]; then
            mv "${f}" "${archive_dir}/"
            moved=1
        fi
    done

    if [ "${moved}" -eq 1 ]; then
        echo "📦 Archived previous runtime state to: ${archive_dir}"
    else
        # Remove empty archive dir if nothing was moved
        rmdir "${archive_dir}" 2>/dev/null || true
        echo "✅ No prior runtime state found to archive."
    fi
}

start_full_stack() {
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         GATEWAY MODE - FULL STACK                            ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  Gateway:   http://localhost:${UA_GATEWAY_PORT}                            ║"
    echo "║  API:       http://localhost:8001                            ║"
    echo "║  Web UI:    http://localhost:3000                            ║"
    echo "║  Dashboard: http://localhost:3000/dashboard                  ║"
    echo "║                                                              ║"
    echo "║  CLI client (separate terminal):                             ║"
    echo "║    UA_GATEWAY_URL=http://localhost:${UA_GATEWAY_PORT} ./start_cli_dev.sh   ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    # Clean up existing processes
    echo "🧹 Cleaning up existing processes..."
    fuser -k "${UA_GATEWAY_PORT}"/tcp 2>/dev/null
    fuser -k 8001/tcp 2>/dev/null
    fuser -k 3000/tcp 2>/dev/null
    sleep 1

    # 1. Start Gateway Server (background)
    echo "🚀 Starting Gateway Server (Port ${UA_GATEWAY_PORT})..."
    run_gateway_background
    GATEWAY_PID=$!
    echo "   PID: $GATEWAY_PID (Logs: tail -f gateway.log)"

    # Wait for gateway to be ready
    echo "⏳ Waiting for Gateway..."
    for i in {1..30}; do
        if curl -s "http://localhost:${UA_GATEWAY_PORT}/api/v1/health" > /dev/null 2>&1; then
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
    UA_GATEWAY_URL="http://localhost:${UA_GATEWAY_PORT}" PYTHONPATH=src uv run python -m universal_agent.api.server > api.log 2>&1 &
    API_PID=$!
    echo "   PID: $API_PID (Logs: tail -f api.log)"
    sleep 2

    # 3. Start Web UI (foreground, optional)
    echo "💻 Starting Web UI (Port 3000)..."
    echo "---------------------------------------------------"
    echo "   Use Ctrl+C to stop all services."
    echo ""
    if [ -d "web-ui" ] && command -v npm >/dev/null 2>&1; then
        cd web-ui
        npm run dev
    else
        echo "⚠️  Web UI skipped (missing web-ui directory or npm)."
    fi
}

case "$MODE" in
    --clean)
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║         CLEAN RUNTIME STATE                                  ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        clean_runtime_state
        echo "✅ Clean complete."
        exit 0
        ;;

    --clean-start)
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║         CLEAN + START FULL STACK                             ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        clean_runtime_state
        start_full_stack
        ;;

    --server)
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║         GATEWAY SERVER ONLY                                  ║"
        echo "╠══════════════════════════════════════════════════════════════╣"
        echo "║  Gateway:   http://localhost:${UA_GATEWAY_PORT}                            ║"
        echo "║                                                              ║"
        echo "║  CLI client (separate terminal):                             ║"
        echo "║    UA_GATEWAY_URL=http://localhost:${UA_GATEWAY_PORT} ./start_cli_dev.sh   ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        echo ""
        run_gateway_foreground
        ;;

    --ui)
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║         WEB UI ONLY (expects gateway on 8002)                ║"
        echo "╠══════════════════════════════════════════════════════════════╣"
        echo "║  Gateway:   $UA_GATEWAY_URL (must be running)                ║"
        echo "║  API:       http://localhost:8001                            ║"
        echo "║  Web UI:    http://localhost:3000                            ║"
        echo "║  Dashboard: http://localhost:3000/dashboard                  ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        echo ""
        
        # Check if gateway is running
        if ! curl -s "http://localhost:${UA_GATEWAY_PORT}/api/v1/health" > /dev/null 2>&1; then
            echo "❌ Gateway server not running on port ${UA_GATEWAY_PORT}"
            echo "   Start it first with: ./start_gateway.sh --server"
            exit 1
        fi
        echo "✅ Gateway server detected on port ${UA_GATEWAY_PORT}"
        
        # Clean up existing processes
        fuser -k 8001/tcp 2>/dev/null
        fuser -k 3000/tcp 2>/dev/null
        sleep 1
        
        # Start API server (connects to gateway)
        echo "🔌 Starting API Server (Port 8001)..."
        UA_GATEWAY_URL="http://localhost:${UA_GATEWAY_PORT}" PYTHONPATH=src uv run python -m universal_agent.api.server > api.log 2>&1 &
        API_PID=$!
        echo "   PID: $API_PID (Logs: tail -f api.log)"
        sleep 2
        
        # Start Web UI (optional)
        echo "💻 Starting Web UI (Port 3000)..."
        if [ -d "web-ui" ] && command -v npm >/dev/null 2>&1; then
            cd web-ui
            npm run dev
        else
            echo "⚠️  Web UI skipped (missing web-ui directory or npm)."
        fi
        ;;

    full|*)
        start_full_stack
        ;;
esac
