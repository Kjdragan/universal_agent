#!/bin/bash
set -e

# Start Agent College (LogfireFetch) in the background
# running on localhost:8000 (Internal Only for now, effectively)
echo "🎓 Starting Agent College Service..."
uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8000 --host 127.0.0.1 &

# Start Telegram Bot (Main Process)
# This will handle the $PORT binding for Webhooks if configured
echo "🤖 Starting Universal Agent Telegram Bot..."
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
exec uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port $PORT
