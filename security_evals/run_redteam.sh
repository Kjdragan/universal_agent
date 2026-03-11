#!/bin/bash
set -e
cd "$(dirname "$0")"

VENV_PYTHON="../.venv/bin/python3"

# ── 1. Load secrets from Infisical ──
eval "$($VENV_PYTHON -c '
import sys, os
sys.path.insert(0, os.path.abspath("../src"))
from universal_agent.infisical_loader import initialize_runtime_secrets
initialize_runtime_secrets()
for k, v in os.environ.items():
    if any(tok in k for tok in ["API_KEY", "API_TOKEN", "UA_INTERNAL", "UA_OPS"]):
        safe_v = v.replace("\"", "\\\"")
        print(f"export {k}=\"{safe_v}\"")
')"

# ── 2. Promptfoo conflict fix ──
# Promptfoo disables remote generation when OPENAI_API_KEY is set.
# We use Anthropic (via Z.AI) for grading, so unset the OpenAI vars.
unset OPENAI_API_KEY
unset OPENAI_BASE_URL

# ── 3. Tell Promptfoo to use the project venv for the Python provider ──
export PYTHONPATH="$(cd .. && pwd)/src"
export PROMPTFOO_PYTHON="$VENV_PYTHON"

echo "🔐 Secrets loaded. ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:+set}"
echo "🚀 Starting Promptfoo red team eval..."
echo "   Make sure UA Gateway is running on port 8002."

# ── 4. Generate fresh attack test cases, then evaluate ──
npx promptfoo@latest redteam generate -c promptfoo_redteam.yaml -o promptfoo_redteam_generated.yaml --no-cache
npx promptfoo@latest redteam eval -c promptfoo_redteam_generated.yaml --no-cache "$@"

echo "✅ Red team eval complete."
echo "   View results: npx promptfoo@latest view"
