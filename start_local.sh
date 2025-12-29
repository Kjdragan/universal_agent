#!/bin/bash
# Universal Agent - Local Startup Script
# Runs the complete system locally (no Docker required)

set -e

cd "$(dirname "$0")"

echo "=============================================="
echo "Universal Agent - Local Startup"
echo "=============================================="

# Check .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found. Copy .env.example and configure it."
    exit 1
fi

# Load .env
set -a
source .env
set +a

# Check required env vars
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN not set in .env"
    exit 1
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "❌ ANTHROPIC_API_KEY not set in .env"
    exit 1
fi

if [ -z "$COMPOSIO_API_KEY" ]; then
    echo "❌ COMPOSIO_API_KEY not set in .env"
    exit 1
fi

# Ensure CRAWL4AI_API_KEY is set (Cloud mode, no Docker needed)
if [ -z "$CRAWL4AI_API_KEY" ]; then
    echo "⚠️  CRAWL4AI_API_KEY not set - crawl_parallel will use local library"
else
    echo "✅ CRAWL4AI_API_KEY set - using Cloud API"
fi

# Activate venv
if [ -d ".venv" ]; then
    echo "🐍 Activating virtual environment..."
    source .venv/bin/activate
else
    echo "❌ .venv not found. Run: uv venv && uv pip install -e ."
    exit 1
fi

# Choose run mode
MODE=${1:-cli}

case $MODE in
    cli)
        echo ""
        echo "🚀 Starting CLI Mode..."
        echo "=============================================="
        python src/universal_agent/main.py
        ;;
    
    bot)
        echo ""
        echo "🚀 Starting Telegram Bot Mode..."
        echo "Port: ${PORT:-8000}"
        echo "=============================================="
        uvicorn src.universal_agent.bot.main:app --host 0.0.0.0 --port ${PORT:-8000}
        ;;

    worker)
        echo ""
        echo "🎓 Starting Agent College Worker..."
        echo "=============================================="
        python src/universal_agent/agent_college/runner.py
        ;;

    full)
        echo ""
        echo "🚀 Starting Full System (Agent + College)..."
        echo "=============================================="
        
        # Start Worker in background
        python src/universal_agent/agent_college/runner.py &
        WORKER_PID=$!
        echo "🎓 Agent College Worker started (PID: $WORKER_PID)"
        
        # Cleanup trap
        trap "echo '🛑 Stopping worker...'; kill $WORKER_PID" EXIT
        
        # Start CLI
        echo "🤖 Starting Agent CLI..."
        python src/universal_agent/main.py
        ;;
    
    *)
        echo "Usage: ./start_local.sh [cli|bot|worker|full]"
        echo "  cli    - Interactive CLI mode"
        echo "  bot    - Telegram webhook server"
        echo "  worker - Agent College background worker"
        echo "  full   - CLI + Agent College (background)"
        exit 1
        ;;
esac
