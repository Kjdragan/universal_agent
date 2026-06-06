#!/usr/bin/env bash
# Install + enable the S5 Phase A batch-3 systemd timers (hourly active-window
# producers). Idempotent — safe to re-run on every deploy. Mirrors
# scripts/install_vps_phase_a_batch2_timers.sh, but batch 3 is a simple
# 2-service / 2-timer shape: hourly-intel-digest and csi-convergence-sync each
# have their own .service + .timer pair, both firing at the top of every
# active-window hour (06..21 America/Chicago).
#
# Double-fire is prevented on the gateway side: gateway_server forces each job's
# in-process registration disabled (_is_migrated_to_systemd; hourly_intel_digest
# via the _register_system_cron_job(enabled=…) gate, csi_convergence_sync via a
# bespoke update_job disable). ROLLBACK: UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1 +
# restart gateway, then `systemctl disable --now <timer>` here.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
SRC="$APP_ROOT/deployment/systemd"

# 2 oneshot services.
SERVICES=(
  "universal-agent-hourly-intel-digest.service"
  "universal-agent-csi-convergence-sync.service"
)
# 2 timers (one per service).
TIMERS=(
  "universal-agent-hourly-intel-digest.timer"
  "universal-agent-csi-convergence-sync.timer"
)

# Validate every unit file is present BEFORE touching systemd.
for f in "${SERVICES[@]}" "${TIMERS[@]}"; do
  if [[ ! -f "$SRC/$f" ]]; then
    echo "Missing required unit file: $SRC/$f" >&2
    exit 2
  fi
done

for f in "${SERVICES[@]}" "${TIMERS[@]}"; do
  install -m 0644 "$SRC/$f" "$SYSTEMD_DIR/$f"
done

systemctl daemon-reload

# enable --now starts the TIMERS (not the oneshot services). We do NOT
# `systemctl start` the services at install — these are scheduled jobs (one
# sends operator email); the timers arm them on their normal slots.
for t in "${TIMERS[@]}"; do
  systemctl enable --now "$t"
done

echo "== Phase A batch-3 timers =="
systemctl list-timers "${TIMERS[@]}" --all --no-pager || true
