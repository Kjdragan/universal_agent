#!/usr/bin/env bash
# Install + enable the S5 Phase A batch-2 systemd timers (content dailies).
# Idempotent — safe to re-run on every deploy. Mirrors
# scripts/install_vps_phase_a_batch1_timers.sh, but batch 2 has a 5-service /
# 7-timer shape: the three proactive-report slots share ONE
# universal-agent-proactive-report.service (driven by three timers), and the
# digest / artifact-reminders-sweep / intel-promoter / codie-cleanup jobs each
# have their own pair. (artifact-reminders-sweep joined 2026-06-08, migrated off
# the in-process gateway cron alongside its sibling proactive-artifact-digest.)
#
# Double-fire is prevented on the gateway side: gateway_server forces each job's
# in-process registration disabled (_is_migrated_to_systemd; codie via a bespoke
# add_job/update_job disable). ROLLBACK: UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1 +
# restart gateway, then `systemctl disable --now <timer>` here.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
SRC="$APP_ROOT/deployment/systemd"

# 5 oneshot services (proactive-report is shared by the three report timers).
SERVICES=(
  "universal-agent-proactive-report.service"
  "universal-agent-proactive-artifact-digest.service"
  "universal-agent-artifact-reminders-sweep.service"
  "universal-agent-intel-auto-promoter.service"
  "universal-agent-codie-proactive-cleanup.service"
)
# 7 timers (three report slots + digest + reminders-sweep + promoter + codie).
TIMERS=(
  "universal-agent-proactive-report-morning.timer"
  "universal-agent-proactive-report-midday.timer"
  "universal-agent-proactive-report-afternoon.timer"
  "universal-agent-proactive-artifact-digest.timer"
  "universal-agent-artifact-reminders-sweep.timer"
  "universal-agent-intel-auto-promoter.timer"
  "universal-agent-codie-proactive-cleanup.timer"
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
# `systemctl start` the services at install — these are scheduled jobs (some
# send operator email); the timers arm them on their normal slots.
for t in "${TIMERS[@]}"; do
  systemctl enable --now "$t"
done

echo "== Phase A batch-2 timers =="
systemctl list-timers "${TIMERS[@]}" --all --no-pager || true
