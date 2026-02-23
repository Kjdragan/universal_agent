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
install_unit "csi-rss-semantic-enrich.service"
install_unit "csi-rss-semantic-enrich.timer"
install_unit "csi-rss-trend-report.service"
install_unit "csi-rss-trend-report.timer"
install_unit "csi-daily-summary.service"
install_unit "csi-daily-summary.timer"
install_unit "csi-hourly-token-report.service"
install_unit "csi-hourly-token-report.timer"

systemctl daemon-reload
systemctl enable --now csi-rss-telegram-digest.timer
systemctl enable --now csi-rss-semantic-enrich.timer
systemctl enable --now csi-rss-trend-report.timer
systemctl enable --now csi-daily-summary.timer
systemctl enable --now csi-hourly-token-report.timer

echo "SYSTEMD_EXTRAS_INSTALLED=1"
systemctl is-active csi-rss-telegram-digest.timer
systemctl is-active csi-rss-semantic-enrich.timer
systemctl is-active csi-rss-trend-report.timer
systemctl is-active csi-daily-summary.timer
systemctl is-active csi-hourly-token-report.timer
