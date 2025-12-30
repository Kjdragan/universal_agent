#!/bin/bash
# Local development startup script
# Runs both Agent College and Bot services

cd "$(dirname "$0")"

# Set PYTHONPATH to include src directory
export PYTHONPATH="${PWD}/src:${PYTHONPATH}"

echo "🎓 Starting Agent College Service (port 8000)..."
uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8000 --host 127.0.0.1 &
COLLEGE_PID=$!

sleep 2  # Give College time to start

echo "🤖 Starting Telegram Bot (port 8080)..."
uv run uvicorn universal_agent.bot.main:app --port 8080 --host 0.0.0.0 &
BOT_PID=$!

echo ""
echo "✅ Services started:"
echo "   Agent College: http://127.0.0.1:8000"
echo "   Bot: http://0.0.0.0:8080"
echo ""
echo "Press Ctrl+C to stop both services"

# Handle cleanup on exit
trap "echo 'Stopping...'; kill $COLLEGE_PID $BOT_PID 2>/dev/null" EXIT

# Wait for any child to exit
wait
