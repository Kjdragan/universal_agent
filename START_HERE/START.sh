#!/bin/bash

cd "$(dirname "$0")/.."

echo "=============================================="
echo "Universal Agent — Start Menu"
echo "=============================================="
echo "Choose a startup mode:"
echo "  1) CLI (Direct, fastest dev loop)"
echo "  2) Gateway Full Stack (Gateway + API + Web UI)"
echo "  3) Advanced: Multi‑terminal Gateway stack (diagnostics)"
echo ""
read -r -p "Enter choice [1-3]: " choice

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
  *)
    echo "Invalid choice. Use 1, 2, or 3."
    exit 1
    ;;
esac
