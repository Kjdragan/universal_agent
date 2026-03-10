#!/bin/bash
set -e

# ═══════════════════════════════════════════════════════════════════════
# Universal Agent Nightly Security Red Team Evaluation
# Runs via cron at 2:00 AM and outputs results to the web-ui dashboard.
#
# Cron entry:
#   0 2 * * * /home/kjdragan/lrepos/universal_agent/security_evals/schedule_redteam.sh >> /home/kjdragan/lrepos/universal_agent/logs/redteam_cron.log 2>&1
# ═══════════════════════════════════════════════════════════════════════

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
VENV_PYTHON="../.venv/bin/python3"

echo "========================================================="
echo "Starting Universal Agent Nightly Red Team Eval"
echo "Time: $(date)"
echo "========================================================="

# ── 1. Load secrets from Infisical via Python SDK ──
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

# ── 2. Unset OpenAI vars (conflicts with Promptfoo remote generation) ──
unset OPENAI_API_KEY
unset OPENAI_BASE_URL

# ── 3. Configure Python path for the provider script ──
export PYTHONPATH="$(cd .. && pwd)/src"
export PROMPTFOO_PYTHON="$VENV_PYTHON"

echo "Secrets loaded. ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:+set}"

# ── 4. Output path ──
REPORT_DIR="../web-ui/public"
REPORT_FILE="${REPORT_DIR}/security_report.json"
mkdir -p "$REPORT_DIR"

# ── 5. Generate attack cases and run evaluation ──
npx promptfoo@latest redteam generate -c promptfoo_redteam.yaml -o promptfoo_redteam_generated.yaml --no-cache
npx promptfoo@latest redteam eval -c promptfoo_redteam_generated.yaml --no-cache -o "$REPORT_FILE"

echo "========================================================="
echo "Red Team Eval Complete. Report saved to: $REPORT_FILE"
echo "Time: $(date)"
echo "========================================================="
