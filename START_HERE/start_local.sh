#!/bin/bash

cd "$(dirname "$0")"

echo "⚠️  start_local.sh is deprecated."
echo "    Use ./start_cli_dev.sh for direct CLI dev"
echo "    or ./start_gateway.sh for the production-like stack." 
echo ""
echo "Deprecated script moved to: start_local.sh.deprecated"
exit 1
