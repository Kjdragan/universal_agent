#!/bin/bash

cd "$(dirname "$0")"

echo "⚠️  start_ui.sh is deprecated."
echo "    Use ./start_gateway.sh --ui (requires gateway running)"
echo "    or ./start_gateway.sh for the full stack."
echo ""
echo "Deprecated script moved to: start_ui.sh.deprecated"
exit 1
