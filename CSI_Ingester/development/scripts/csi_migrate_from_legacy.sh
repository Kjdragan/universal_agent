#!/usr/bin/env bash
set -euo pipefail

echo "CSI migration helper"
echo "===================="
echo
echo "Reference runbook:"
echo "  CSI_Ingester/documentation/06_CSI_VPS_Deployment_Runbook_v1_2026-02-22.md"
echo
echo "1) Preflight (strict)"
echo "  CSI_Ingester/development/scripts/csi_run.sh CSI_Ingester/development/scripts/csi_preflight.sh --strict"
echo
echo "2) Smoke CSI->UA signed ingest"
echo "  CSI_Ingester/development/scripts/csi_run.sh uv run python CSI_Ingester/development/scripts/csi_emit_smoke_event.py --require-internal-dispatch"
echo
echo "3) Start CSI service in parallel-run mode (legacy timer still enabled)"
echo "  systemctl enable --now csi-ingester"
echo
echo "4) Validate 24h parallel run snapshot"
echo "  CSI_Ingester/development/scripts/csi_run.sh python CSI_Ingester/development/scripts/csi_parallel_validate.py --db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db --since-minutes 1440"
echo
echo "5) Cut over from legacy poller timer"
echo "  systemctl stop universal-agent-youtube-playlist-poller.timer"
echo "  systemctl disable universal-agent-youtube-playlist-poller.timer"
echo "  systemctl stop universal-agent-youtube-playlist-poller.service"
echo
echo "6) Rollback (if required)"
echo "  systemctl stop csi-ingester"
echo "  systemctl enable --now universal-agent-youtube-playlist-poller.timer"
