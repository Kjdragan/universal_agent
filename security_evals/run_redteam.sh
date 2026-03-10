#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "🔐 Proceeding with Universal Agent Security Red Team Evaluation..."
echo "Starting evaluation with promptfoo. Make sure UA Gateway is running on port 8002."

# Install/run promptfoo to build the attack dataset and execute it against the UA gateway
# The redteam init and setup can be done by simply running the main CLI
npx promptfoo@latest eval -c promptfoo_redteam.yaml

# To view the interactive dashboard in a browser:
# npx promptfoo@latest view
