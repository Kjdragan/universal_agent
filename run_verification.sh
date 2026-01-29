#!/bin/bash
#
# Verification Runner
# Usage: ./run_verification.sh [--full]

MODE="${1:-smoke}"
TEST_DIR="tests/stabilization"

echo "🧪 Starting Verification Run..."
echo "----------------------------------------"

if [ "$MODE" == "--full" ]; then
    echo "🐢 Running FULL Golden Parity Suite (Estimated: 3-5m)"
    echo "Status: Tier 2 tests are not yet implemented in pytest."
    echo "        Run manually via: ./start_cli_dev.sh 'Russia-Ukraine prompt'"
    exit 0
else
    echo "⚡ Running FAST Smoke Tests (Estimated: <30s)"
    
    # Run pytest on the stabilization directory
    # -v: verbose
    # -s: show stdout (useful for debugging)
    PYTHONPATH=src uv run pytest "$TEST_DIR" -v -s
    
    EXIT_CODE=$?
    
    echo "----------------------------------------"
    if [ $EXIT_CODE -eq 0 ]; then
        echo "✅ Smoke Tests PASSED"
    else
        echo "❌ Smoke Tests FAILED"
    fi
    
    exit $EXIT_CODE
fi
