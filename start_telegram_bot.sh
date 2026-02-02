#!/bin/bash
# ============================================================
# Universal Agent Telegram Bot - Startup Script
# ============================================================
# This script handles all the startup steps for you:
# 1. Starts ngrok to expose port 8000
# 2. Waits for ngrok to be ready
# 3. Updates the webhook URL
# 4. Starts/restarts the Docker container
# 5. Registers the webhook with Telegram
# ============================================================

set -e  # Exit on error

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "🚀 Starting Universal Agent Telegram Bot..."
echo "============================================"
echo "ℹ️  Local dev override: enabling in-process gateway."
export UA_TELEGRAM_ALLOW_INPROCESS=1

# --- Step 1: Kill any existing ngrok ---
echo "🔪 Stopping any existing ngrok processes..."
pkill ngrok 2>/dev/null || true
sleep 1

# --- Step 2: Start ngrok in background ---
echo "🌐 Starting ngrok tunnel on port 8000..."
ngrok http 8000 > /dev/null 2>&1 &
NGROK_PID=$!
echo "   ngrok PID: $NGROK_PID"

# --- Step 3: Wait for ngrok and get the URL ---
echo "⏳ Waiting for ngrok to start..."
sleep 3

# Get the public URL from ngrok's API
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | python3 -c "import sys, json; tunnels = json.load(sys.stdin).get('tunnels', []); print(tunnels[0]['public_url'] if tunnels else '')" 2>/dev/null)

if [ -z "$NGROK_URL" ]; then
    echo "❌ Failed to get ngrok URL. Is ngrok running?"
    echo "   Try running 'ngrok http 8000' manually in another terminal."
    exit 1
fi

WEBHOOK_URL="${NGROK_URL}/webhook"
echo "✅ Ngrok URL: $NGROK_URL"
echo "✅ Webhook URL: $WEBHOOK_URL"

# --- Step 4: Update .env file with new URL ---
echo "📝 Updating .env with new WEBHOOK_URL..."
# Use sed to replace the WEBHOOK_URL line
if grep -q "^WEBHOOK_URL=" .env; then
    sed -i "s|^WEBHOOK_URL=.*|WEBHOOK_URL=\"$WEBHOOK_URL\"|" .env
else
    echo "WEBHOOK_URL=\"$WEBHOOK_URL\"" >> .env
fi

# --- Step 5: Start Docker container ---
echo "🐳 Starting Docker container..."
docker-compose down 2>/dev/null || true
docker network prune -f 2>/dev/null || true
docker-compose up -d

echo "⏳ Waiting for container to be healthy..."
sleep 5

# --- Step 6: Register webhook ---
echo "📞 Registering webhook with Telegram..."
docker cp "$PROJECT_DIR/register_webhook.py" universal_agent_bot:/app/register_webhook.py
docker exec universal_agent_bot python /app/register_webhook.py

echo ""
echo "============================================"
echo "✅ ALL SYSTEMS GO!"
echo "============================================"
echo ""
echo "📱 NOW GO TO TELEGRAM AND:"
echo "   1. Open your bot chat"
echo "   2. Send: /start"
echo "   3. Send a task: /agent <your request>"
echo ""
echo "🛑 To stop everything later, run:"
echo "   docker-compose down && pkill ngrok"
echo ""
echo "📋 Ngrok is running in the background (PID: $NGROK_PID)"
echo "   View ngrok dashboard: http://localhost:4040"
echo ""
