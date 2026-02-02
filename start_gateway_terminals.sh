#!/bin/bash

cd "$(dirname "$0")"

echo "⚠️  start_gateway_terminals.sh is deprecated."
echo "    Use ./start_gateway.sh (full stack)" 
echo "    or ./start_gateway.sh --server (gateway only)."
echo ""
echo "Deprecated script moved to: start_gateway_terminals.sh.deprecated"
exit 1
