#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/opt/universal_agent/CSI_Ingester/development"
SRC_DIR="${ROOT_DIR}/deployment/systemd"

install_unit() {
  local name="$1"
  cp "${SRC_DIR}/${name}" "/etc/systemd/system/${name}"
}

install_unit "csi-rss-telegram-digest.service"
install_unit "csi-rss-telegram-digest.timer"
install_unit "csi-reddit-telegram-digest.service"
install_unit "csi-reddit-telegram-digest.timer"
install_unit "csi-playlist-tutorial-digest.service"
install_unit "csi-playlist-tutorial-digest.timer"
install_unit "csi-rss-semantic-enrich.service"
install_unit "csi-rss-semantic-enrich.timer"
install_unit "csi-rss-trend-report.service"
install_unit "csi-rss-trend-report.timer"
install_unit "csi-reddit-trend-report.service"
install_unit "csi-reddit-trend-report.timer"
install_unit "csi-rss-insight-analyst.service"
install_unit "csi-rss-insight-analyst.timer"
install_unit "csi-rss-reclassify-categories.service"
install_unit "csi-rss-reclassify-categories.timer"
install_unit "csi-category-quality-loop.service"
install_unit "csi-category-quality-loop.timer"
install_unit "csi-analysis-task-runner.service"
install_unit "csi-analysis-task-runner.timer"
install_unit "csi-analysis-task-bootstrap.service"
install_unit "csi-analysis-task-bootstrap.timer"
install_unit "csi-rss-quality-gate.service"
install_unit "csi-rss-quality-gate.timer"
install_unit "csi-replay-dlq.service"
install_unit "csi-replay-dlq.timer"
install_unit "csi-report-product-finalize.service"
install_unit "csi-report-product-finalize.timer"
install_unit "csi-daily-summary.service"
install_unit "csi-daily-summary.timer"
install_unit "csi-hourly-token-report.service"
install_unit "csi-hourly-token-report.timer"
install_unit "csi-delivery-health-canary.service"
install_unit "csi-delivery-health-canary.timer"
install_unit "csi-delivery-health-auto-remediate.service"
install_unit "csi-delivery-health-auto-remediate.timer"
install_unit "csi-delivery-slo-gatekeeper.service"
install_unit "csi-delivery-slo-gatekeeper.timer"
install_unit "csi-threads-token-refresh-sync.service"
install_unit "csi-threads-token-refresh-sync.timer"
install_unit "csi-threads-rollout-verify.service"
install_unit "csi-threads-rollout-verify.timer"
install_unit "csi-threads-publish-canary-verify.service"
install_unit "csi-threads-publish-canary-verify.timer"
install_unit "csi-reddit-semantic-enrich.service"
install_unit "csi-reddit-semantic-enrich.timer"
install_unit "csi-threads-semantic-enrich.service"
install_unit "csi-threads-semantic-enrich.timer"
install_unit "csi-threads-trend-report.service"
install_unit "csi-threads-trend-report.timer"
install_unit "csi-global-trend-brief.service"
install_unit "csi-global-trend-brief.timer"
install_unit "csi-global-brief-reminder.service"
install_unit "csi-global-brief-reminder.timer"
install_unit "csi-db-backup.service"
install_unit "csi-db-backup.timer"

systemctl daemon-reload
systemctl enable --now csi-rss-telegram-digest.timer
systemctl enable --now csi-reddit-telegram-digest.timer
systemctl enable --now csi-playlist-tutorial-digest.timer
systemctl enable --now csi-rss-semantic-enrich.timer
systemctl enable --now csi-rss-trend-report.timer
systemctl enable --now csi-reddit-trend-report.timer
systemctl enable --now csi-rss-insight-analyst.timer
systemctl enable --now csi-rss-reclassify-categories.timer
systemctl enable --now csi-category-quality-loop.timer
systemctl enable --now csi-analysis-task-runner.timer
systemctl enable --now csi-analysis-task-bootstrap.timer
systemctl enable --now csi-rss-quality-gate.timer
systemctl enable --now csi-replay-dlq.timer
systemctl enable --now csi-report-product-finalize.timer
systemctl enable --now csi-daily-summary.timer
systemctl enable --now csi-hourly-token-report.timer
systemctl enable --now csi-delivery-health-canary.timer
systemctl enable --now csi-delivery-health-auto-remediate.timer
systemctl enable --now csi-delivery-slo-gatekeeper.timer
systemctl enable --now csi-threads-token-refresh-sync.timer
systemctl enable --now csi-threads-rollout-verify.timer
systemctl enable --now csi-threads-publish-canary-verify.timer
systemctl enable --now csi-reddit-semantic-enrich.timer
systemctl enable --now csi-threads-semantic-enrich.timer
systemctl enable --now csi-threads-trend-report.timer
systemctl enable --now csi-global-trend-brief.timer
systemctl enable --now csi-global-brief-reminder.timer
systemctl enable --now csi-db-backup.timer

echo "SYSTEMD_EXTRAS_INSTALLED=1"
systemctl is-active csi-rss-telegram-digest.timer
systemctl is-active csi-reddit-telegram-digest.timer
systemctl is-active csi-playlist-tutorial-digest.timer
systemctl is-active csi-rss-semantic-enrich.timer
systemctl is-active csi-rss-trend-report.timer
systemctl is-active csi-reddit-trend-report.timer
systemctl is-active csi-rss-insight-analyst.timer
systemctl is-active csi-rss-reclassify-categories.timer
systemctl is-active csi-category-quality-loop.timer
systemctl is-active csi-analysis-task-runner.timer
systemctl is-active csi-analysis-task-bootstrap.timer
systemctl is-active csi-rss-quality-gate.timer
systemctl is-active csi-replay-dlq.timer
systemctl is-active csi-report-product-finalize.timer
systemctl is-active csi-daily-summary.timer
systemctl is-active csi-hourly-token-report.timer
systemctl is-active csi-delivery-health-canary.timer
systemctl is-active csi-delivery-health-auto-remediate.timer
systemctl is-active csi-delivery-slo-gatekeeper.timer
systemctl is-active csi-threads-token-refresh-sync.timer
systemctl is-active csi-threads-rollout-verify.timer
systemctl is-active csi-threads-publish-canary-verify.timer
systemctl is-active csi-reddit-semantic-enrich.timer
systemctl is-active csi-threads-semantic-enrich.timer
systemctl is-active csi-threads-trend-report.timer
systemctl is-active csi-global-trend-brief.timer
systemctl is-active csi-global-brief-reminder.timer
systemctl is-active csi-db-backup.timer
