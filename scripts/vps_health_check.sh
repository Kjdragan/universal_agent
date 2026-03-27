#!/usr/bin/env bash
# VPS Health Check — single-source, runs locally or via SSH
# Usage: ./vps_health_check.sh              (local)
#        ssh ua@uaonvps 'bash -s' < vps_health_check.sh  (remote)
set -euo pipefail

SEP="---"
cores=$(nproc 2>/dev/null || echo 4)

collect() {
  uptime
  echo "$SEP"
  echo "$cores"
  echo "$SEP"
  free -h
  echo "$SEP"
  df -h /
  echo "$SEP"
  du -sh /opt/universal_agent/AGENT_RUN_WORKSPACES/ 2>/dev/null || echo "N/A"
  echo "$SEP"
  ps aux | grep -E 'edgar\.ai|claude' | grep -v grep | wc -l
  echo "$SEP"
  ls -lh /opt/universal_agent/AGENT_RUN_WORKSPACES/*.db 2>/dev/null || echo "No DB files"
  echo "$SEP"
  systemctl status universal-agent-gateway --no-pager 2>/dev/null | head -5 || echo "Service not found"
  echo "$SEP"
  journalctl -u universal-agent-gateway --since '30 min ago' --no-pager 2>/dev/null | grep -ci 'error\|exception\|locked' || echo "0"
  echo "$SEP"
  grep 'UA_HOOKS_AGENT_DISPATCH_CONCURRENCY' /opt/universal_agent/.env 2>/dev/null || echo "NOT_SET"
}

collect
