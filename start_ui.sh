#!/bin/bash
# Universal Agent - UI Startup Script
# Starts the Backend API and the Next.js Frontend

set -e

# Change to project root directory
cd "$(dirname "$0")"

echo "=============================================="
echo "🚀 Creating/Starting Universal Agent UI"
echo "=============================================="

# 1. Check for Prerequisites
if ! command -v uv &> /dev/null; then
    echo "❌ 'uv' is not installed. Please install it first."
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo "❌ 'npm' is not installed. Please install Node.js."
    exit 1
fi

# 2. Setup Cleanup Trap
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    
    if [ ! -z "$BACKEND_PID" ]; then
        echo "   Killing Backend (PID: $BACKEND_PID)..."
        kill $BACKEND_PID 2>/dev/null || true
    fi
    
    if [ ! -z "$FRONTEND_PID" ]; then
        echo "   Killing Frontend (PID: $FRONTEND_PID)..."
        kill $FRONTEND_PID 2>/dev/null || true
    fi
    
    echo "✅ Done."
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# 3. Start Backend Server
echo ""
echo "🐍 Starting Backend API (Port 8001)..."
# Using unbuffered output for Python to see logs immediately
PYTHONUNBUFFERED=1 PYTHONPATH=src uv run python src/universal_agent/api/server.py &
BACKEND_PID=$!
echo "   Backend running with PID: $BACKEND_PID"

# Wait a moment for backend to potentially fail early
sleep 2
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Backend failed to start immediately. Check logs."
    exit 1
fi

# 4. Start Frontend
echo ""
echo "⚛️  Starting Web UI (Port 3000)..."
cd web-ui

# Check if node_modules exists, install if missing
if [ ! -d "node_modules" ]; then
    echo "   📦 Installing frontend dependencies..."
    npm install
fi

# Start Next.js dev server
npm run dev &
FRONTEND_PID=$!
echo "   Frontend running with PID: $FRONTEND_PID"

echo ""
echo "=============================================="
echo "✅ Universal Agent UI is running!"
echo "   Backend:  http://localhost:8001"
echo "   Frontend: http://localhost:3000 (or 3001 if 3000 is taken)"
echo "   Press Ctrl+C to stop both servers."
echo "=============================================="

# Wait for both processes
wait $BACKEND_PID $FRONTEND_PID
