#!/bin/bash
# kickoff_harness.sh
# Usage: ./kickoff_harness.sh [PLAN_FILE] [OBJECTIVE]

PLAN_FILE=${1:-"recovered_plan.json"}
OBJECTIVE=${2:-"Resume previous plan"}

if [ ! -f "$PLAN_FILE" ]; then
    echo "❌ Error: Plan file '$PLAN_FILE' not found!"
    echo "Usage: ./kickoff_harness.sh <PLAN_JSON> <OBJECTIVE>"
    exit 1
fi

echo "🚀 Kicking off Harness with Plan: $PLAN_FILE"
echo "🎯 Objective: $OBJECTIVE"
echo "=============================================="

./start_cli_dev.sh --harness "$OBJECTIVE" --harness-template "$PLAN_FILE"
