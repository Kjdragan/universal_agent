#!/bin/bash

# launch_dashboard.sh - Automates starting the UA stack and opening the dashboard

cd "$(dirname "$0")"

echo "🚀 Launching Universal Agent Dashboard..."

# 1. Start everything in the background if not already running
# We check if port 3000 is already active
if ! lsof -i :3000 > /dev/null 2>&1; then
    echo "📦 Starting Gateway Full Stack..."
    ./start_gateway.sh &
    STACK_PID=$!
else
    echo "✅ Stack already running."
fi

# 2. Wait for Web UI to be ready
echo "⏳ Waiting for Dashboard UI (localhost:3000)..."
MAX_ATTEMPTS=60
for ((i=1; i<=MAX_ATTEMPTS; i++)); do
    if curl -s http://localhost:3000 > /dev/null 2>&1; then
        echo "   ✅ Dashboard ready."
        break
    fi
    sleep 2
    if [ $i -eq $MAX_ATTEMPTS ]; then
        echo "   ❌ Timeout waiting for Dashboard."
        exit 1
    fi
done

# 3. Open Browser
echo "🌐 Opening Browser to Dashboard..."
if command -v xdg-open > /dev/null 2>&1; then
    xdg-open "http://localhost:3000/dashboard" > /dev/null 2>&1 &
elif command -v open > /dev/null 2>&1; then
    open "http://localhost:3000/dashboard"
else
    echo "⚠️  Could not find xdg-open or open. Please navigate to:"
    echo "   http://localhost:3000/dashboard"
fi

# 4. Keep script alive if we started the stack
if [ -z "$STACK_PID" ]; then
    echo "✨ Done."
else
    echo "---------------------------------------------------"
    echo "   Dashboard is running in the background."
    echo "   Use Ctrl+C to stop services."
    echo ""
    # Trap SIGINT to kill background stack
    trap 'kill $STACK_PID; exit' SIGINT
    wait $STACK_PID
fi
