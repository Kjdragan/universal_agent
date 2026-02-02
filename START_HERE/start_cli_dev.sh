#!/bin/bash
#
# CLI Direct Mode - Fast Development & Testing
#
# This runs the CLI in direct mode (no gateway overhead).
# Uses the canonical process_turn() engine directly.
#
# Benefits:
# - Fastest startup (no gateway session setup)
# - Same execution engine as gateway mode
# - Good for rapid iteration and debugging
#
# Usage:
#   ./start_cli_dev.sh                    # Interactive mode
#   ./start_cli_dev.sh "your prompt"      # Single query mode (pipes prompt)
#   ./start_cli_dev.sh --harness "objective"  # Harness mode
#

cd "$(dirname "$0")"

# Keep uv cache inside repo
export UV_CACHE_DIR="$(pwd)/.uv-cache"

echo "🚀 Universal Agent CLI (Direct Mode)"
echo "---------------------------------------------------"

# Check if first arg is a flag (starts with -)
if [ -n "$1" ] && [[ "$1" == -* ]]; then
    # Pass flags directly to main.py
    PYTHONPATH=src uv run python -m universal_agent.main "$@"
elif [ -n "$1" ]; then
    # Single query mode - pipe the prompt to interactive CLI
    # Use printf to send prompt + quit command
    echo "📝 Running single query: $1"
    echo ""
    printf '%s\nquit\n' "$1" | PYTHONPATH=src uv run python -m universal_agent.main
else
    # Interactive mode
    echo "⌨️  Interactive mode. Type 'quit' or Ctrl+D to exit."
    echo ""
    PYTHONPATH=src uv run python -m universal_agent.main
fi
