#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/opt/universal_agent/CSI_Ingester/development"
SRC_DIR="${ROOT_DIR}/deployment/systemd"

# The unit-list below has drifted from on-disk source files (some units were
# removed in cleanup commits but their entries here weren't). Without
# tolerance for missing files, the first dead reference would abort the
# whole script under `set -e`, leaving every later unit uninstalled. That
# regression masked the 2026-03/05 YouTube transcript outage for ~53 days.
install_unit() {
  local name="$1"
  if [ -f "${SRC_DIR}/${name}" ]; then
    cp "${SRC_DIR}/${name}" "/etc/systemd/system/${name}"
  else
    echo "WARN: install_unit skipping ${name} — source missing at ${SRC_DIR}/${name}"
  fi
}

enable_timer_if_installed() {
  local name="$1"
  if [ -f "/etc/systemd/system/${name}" ]; then
    systemctl enable --now "${name}" || echo "WARN: failed to enable ${name}"
  else
    echo "WARN: skipping enable for ${name} — not installed in /etc/systemd/system"
  fi
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
install_unit "csi-threads-webhook-canary-verify.service"
install_unit "csi-threads-webhook-canary-verify.timer"
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
install_unit "csi-youtube-transcript-canary.service"
install_unit "csi-youtube-transcript-canary.timer"

systemctl daemon-reload

# enable_timer_if_installed silently warns on dead references rather than
# aborting under `set -e`. Every timer name below should still be listed
# so freshly-installed units get auto-enabled the next deploy.
enable_timer_if_installed "csi-rss-telegram-digest.timer"
enable_timer_if_installed "csi-reddit-telegram-digest.timer"
enable_timer_if_installed "csi-playlist-tutorial-digest.timer"
enable_timer_if_installed "csi-rss-semantic-enrich.timer"
enable_timer_if_installed "csi-rss-trend-report.timer"
enable_timer_if_installed "csi-reddit-trend-report.timer"
enable_timer_if_installed "csi-rss-insight-analyst.timer"
enable_timer_if_installed "csi-rss-reclassify-categories.timer"
enable_timer_if_installed "csi-category-quality-loop.timer"
enable_timer_if_installed "csi-analysis-task-runner.timer"
enable_timer_if_installed "csi-analysis-task-bootstrap.timer"
enable_timer_if_installed "csi-rss-quality-gate.timer"
enable_timer_if_installed "csi-replay-dlq.timer"
enable_timer_if_installed "csi-report-product-finalize.timer"
enable_timer_if_installed "csi-daily-summary.timer"
enable_timer_if_installed "csi-hourly-token-report.timer"
enable_timer_if_installed "csi-delivery-health-canary.timer"
enable_timer_if_installed "csi-delivery-health-auto-remediate.timer"
enable_timer_if_installed "csi-delivery-slo-gatekeeper.timer"
enable_timer_if_installed "csi-threads-token-refresh-sync.timer"
enable_timer_if_installed "csi-threads-rollout-verify.timer"
enable_timer_if_installed "csi-threads-publish-canary-verify.timer"
enable_timer_if_installed "csi-threads-webhook-canary-verify.timer"
enable_timer_if_installed "csi-reddit-semantic-enrich.timer"
enable_timer_if_installed "csi-threads-semantic-enrich.timer"
enable_timer_if_installed "csi-threads-trend-report.timer"
enable_timer_if_installed "csi-global-trend-brief.timer"
enable_timer_if_installed "csi-global-brief-reminder.timer"
enable_timer_if_installed "csi-db-backup.timer"
enable_timer_if_installed "csi-youtube-transcript-canary.timer"

echo "SYSTEMD_EXTRAS_INSTALLED=1"
