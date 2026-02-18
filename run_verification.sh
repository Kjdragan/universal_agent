#!/bin/bash
#
# Verification Runner
# Usage: ./run_verification.sh [--full]

MODE="${1:-smoke}"
SMOKE_TEST_PATHS=(
  "tests/stabilization"
  "tests/bot/test_telegram_gateway.py"
  "tests/bot/test_task_manager.py"
  "tests/unit/test_telegram_formatter.py"
)

echo "🧪 Starting Verification Run..."
echo "----------------------------------------"

if [ "$MODE" == "--full" ]; then
    echo "🐢 Running FULL Golden Parity Suite (Estimated: 3-5m)"
    echo "Status: Tier 2 tests are not yet implemented in pytest."
    echo "        Run manually via: ./start_cli_dev.sh 'Russia-Ukraine prompt'"
    exit 0
else
    echo "⚡ Running FAST Smoke Tests (Estimated: <30s)"
    
    # Run pytest on smoke stabilization + Telegram regression coverage
    # -v: verbose
    # -s: show stdout (useful for debugging)
    PYTHONPATH=src uv run pytest "${SMOKE_TEST_PATHS[@]}" -v -s
    
    EXIT_CODE=$?
    
    echo "----------------------------------------"
    if [ $EXIT_CODE -eq 0 ]; then
        echo "✅ Smoke Tests PASSED"
    else
        echo "❌ Smoke Tests FAILED"
    fi
    
    exit $EXIT_CODE
fi
