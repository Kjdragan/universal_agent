#!/usr/bin/env bash
# Idempotent installer for the CSI lane's systemd units.
# Invoked from .github/workflows/deploy.yml on every push to main.
#
# Three responsibilities:
#   1. Install every unit file in deployment/systemd/ to /etc/systemd/system/.
#   2. Enable + start every timer (the .timer units in the same directory).
#   3. Sweep /etc/systemd/system/ for csi-* units that we no longer own and
#      disable+remove them so they stop failing silently every N minutes.
#
# Background on #3: prior to 2026-05-17 the repo carried 36 install_unit
# references to unit files that had been deleted in cleanup commits but never
# removed from the install list. The first dead reference tripped `set -e` and
# aborted the script before any timer got enabled — that's why the YouTube
# transcript pipeline silently rotted for 53 days. Tolerance + sweep make the
# install path self-healing.
set -euo pipefail

ROOT_DIR="/opt/universal_agent/CSI_Ingester/development"
SRC_DIR="${ROOT_DIR}/deployment/systemd"
TARGET_DIR="/etc/systemd/system"

# ── helpers ────────────────────────────────────────────────────────────────
install_unit() {
  local name="$1"
  if [ -f "${SRC_DIR}/${name}" ]; then
    cp "${SRC_DIR}/${name}" "${TARGET_DIR}/${name}"
  else
    echo "WARN: install_unit skipping ${name} — source missing at ${SRC_DIR}/${name}"
  fi
}

enable_timer_if_installed() {
  local name="$1"
  if [ -f "${TARGET_DIR}/${name}" ]; then
    systemctl enable --now "${name}" || echo "WARN: failed to enable ${name}"
  else
    echo "WARN: skipping enable for ${name} — not installed in ${TARGET_DIR}"
  fi
}

# ── canonical list ─────────────────────────────────────────────────────────
# Derived from `ls deployment/systemd/`. Update *here* when adding/removing
# a unit, then the next deploy installs it and the sweep block cleans up
# anything still living on the VPS from a prior generation.
# RETIRED 2026-06-13: csi-rss-trend-report, csi-global-trend-brief, and
# csi-threads-trend-report (.service + .timer) were intentionally retired — their
# trend-report output duplicated the convergence pipeline. Removed from this list
# AND deleted from deployment/systemd/, so the orphan sweep below disables+removes
# them on deploy (a bare `systemctl disable` would otherwise be re-enabled here on
# the next deploy). Do not re-add. See project_docs/04_intelligence/01_csi_architecture.md §3.6.
CANONICAL_UNITS=(
  csi-daily-summary.service
  csi-daily-summary.timer
  csi-db-backup.service
  csi-db-backup.timer
  csi-ingester.service
  csi-quality-assessment.service
  csi-quality-assessment.timer
  csi-replay-dlq.service
  csi-replay-dlq.timer
  csi-rss-semantic-enrich.service
  csi-rss-semantic-enrich.timer
  csi-threads-semantic-enrich.service
  csi-threads-semantic-enrich.timer
  csi-threads-token-refresh-sync.service
  csi-threads-token-refresh-sync.timer
  csi-youtube-transcript-canary.service
  csi-youtube-transcript-canary.timer
)

# Units managed elsewhere — never sweep them even if they aren't in the
# canonical list above. `csi.target` is a pseudo-unit installed during initial
# provisioning, not shipped as a file in deployment/systemd/.
# NOTE: csi-ingester.service is now in CANONICAL_UNITS above. It used to be here
# on the false premise that "a separate deploy step installs it" — in fact
# nothing did, so edits to the unit (e.g. the csi_run.sh wrapper that gives
# batch_brief its LLM key) never reached /etc and had to be cp'd by hand.
EXEMPT_UNITS=(
  csi.target
)

# ── install ────────────────────────────────────────────────────────────────
for unit in "${CANONICAL_UNITS[@]}"; do
  install_unit "$unit"
done

systemctl daemon-reload

# ── enable timers ──────────────────────────────────────────────────────────
for unit in "${CANONICAL_UNITS[@]}"; do
  [[ "$unit" == *.timer ]] || continue
  enable_timer_if_installed "$unit"
done

# ── orphan sweep ───────────────────────────────────────────────────────────
# Disable + remove any csi-*.{service,timer} in /etc/systemd/system/ that
# isn't in the canonical or exempt list. Without this, deleted units linger
# and continue to fire (e.g. csi-rss-quality-gate.service was failing every
# 15 min after its backing script was deleted in commit b4248fc7).
keep_set=" "
for u in "${CANONICAL_UNITS[@]}" "${EXEMPT_UNITS[@]}"; do
  keep_set+="$u "
done

shopt -s nullglob
swept=0
for live in "${TARGET_DIR}"/csi-*.service "${TARGET_DIR}"/csi-*.timer; do
  name=$(basename "$live")
  if [[ "$keep_set" != *" $name "* ]]; then
    echo "ORPHAN_SWEEP disabling+removing ${name}"
    systemctl disable --now "${name}" 2>/dev/null || true
    rm -f "${live}"
    swept=$((swept + 1))
  fi
done
shopt -u nullglob

if [ "$swept" -gt 0 ]; then
  systemctl daemon-reload
fi
echo "ORPHAN_SWEEP_TOTAL=${swept}"

echo "SYSTEMD_EXTRAS_INSTALLED=1"
