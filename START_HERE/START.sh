#!/bin/bash

cd "$(dirname "$0")/.."

echo "======================================================"
echo "Universal Agent — Start Menu"
echo "======================================================"
echo "Choose a startup mode:"
echo "  1) CLI (Direct, fastest dev loop)"
echo "  2) Gateway Full Stack (Gateway + API + Web UI)"
echo "  3) Advanced: Multi‑terminal Gateway stack (diagnostics)"
echo "  4) Telegram Bot (Standalone, uses In-Process Gateway)"
echo ""
read -r -p "Enter choice [1-4]: " choice

echo ""
case "$choice" in
  1)
    ./start_cli_dev.sh
    ;;
  2)
    ./start_gateway.sh
    ;;
  3)
    echo "⚠️  Advanced mode: requires gnome-terminal and is best for diagnostics."
    "./START_HERE/start_gateway_terminals.sh"
    ;;
  4)
    echo "🤖 Starting Telegram Bot..."
    uv run python -m src.universal_agent.bot.main
    ;;
  *)
    echo "Invalid choice. Use 1, 2, 3, or 4."
    exit 1
    ;;
esac
