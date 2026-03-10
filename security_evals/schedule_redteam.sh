#!/bin/bash
set -e

# Universal Agent Nightly Security Red Team Execution
# This script is intended to be run via cron, utilizing Infisical to inject 
# the necessary environment variables securely (never via .env files).
#
# 0 2 * * * /home/kjdragan/lrepos/universal_agent/security_evals/schedule_redteam.sh >> /home/kjdragan/lrepos/universal_agent/logs/redteam_cron.log 2>&1

# Add common local paths to PATH as cron runs with a restricted PATH
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

cd "$(dirname "$0")"

echo "========================================================="
echo "Starting Universal Agent Nightly Red Team Eval"
echo "Time: $(date)"
echo "========================================================="

# Ensure the UA Gateway is accessible. If it is running continuously as a systemd service 
# or docker container, we just run the eval. If not, you may need logic here to start it.

# Define the output path for the structured JSON report
REPORT_DIR="../web-ui/public"
REPORT_FILE="${REPORT_DIR}/security_report.json"

mkdir -p "$REPORT_DIR"

# Execute Promptfoo Red Team eval via npx, piping it through infisical
# --env=kevins-desktop ensures we grab the API keys from Kevin's Infisical parameter service.
# -o specifies the output format
infisical run --env=kevins-desktop -- npx promptfoo@latest eval -c promptfoo_redteam.yaml -o "$REPORT_FILE"

echo "========================================================="
echo "Red Team Eval Complete. Report saved to: $REPORT_FILE"
echo "========================================================="
