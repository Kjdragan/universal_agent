#!/usr/bin/env bash
# Install + enable the S5 Phase A batch-A4 systemd timers (SECRET-BEARING jobs).
# Idempotent — safe to re-run on every deploy. Mirrors
# scripts/install_vps_phase_a_batch3_timers.sh, but batch A4 is a 7-service /
# 7-timer shape: each job has its own .service + .timer pair. These are the
# highest-care jobs — they hold YouTube OAuth tokens, NotebookLM cookies,
# UA_OPS_TOKEN, and an Anthropic key. The morning and evening briefings share
# briefings_agent.py but run as SEPARATE units (the evening ExecStart passes
# --mode=evening), so they cannot share one service cleanly.
#
# Double-fire is prevented on the gateway side: gateway_server forces each job's
# in-process registration disabled (_is_migrated_to_systemd). Gate mechanisms
# differ: youtube_daily_digest + youtube_gold_channel_poller disable via a
# bespoke _find_cron_job_by_system_job/update_job path in their _ensure_* fns
# (like codie_proactive_cleanup); the other five disable via the
# _register_system_cron_job(enabled=…) gate. ROLLBACK:
# UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1 + restart gateway, then
# `systemctl disable --now <timer>` here.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
SRC="$APP_ROOT/deployment/systemd"

# 7 oneshot services (one per job; morning + evening briefing are distinct).
SERVICES=(
  "universal-agent-youtube-daily-digest.service"
  "universal-agent-youtube-gold-channel-poller.service"
  "universal-agent-youtube-oauth-watchdog.service"
  "universal-agent-nightly-wiki.service"
  "universal-agent-morning-briefing.service"
  "universal-agent-evening-briefing.service"
  "universal-agent-csi-demo-triage-rank.service"
)
# 7 timers (one per service).
TIMERS=(
  "universal-agent-youtube-daily-digest.timer"
  "universal-agent-youtube-gold-channel-poller.timer"
  "universal-agent-youtube-oauth-watchdog.timer"
  "universal-agent-nightly-wiki.timer"
  "universal-agent-morning-briefing.timer"
  "universal-agent-evening-briefing.timer"
  "universal-agent-csi-demo-triage-rank.timer"
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
# `systemctl start` the services at install — these are scheduled jobs (several
# send operator email / touch OAuth tokens); the timers arm them on their
# normal slots.
for t in "${TIMERS[@]}"; do
  systemctl enable --now "$t"
done

echo "== Phase A batch-A4 timers =="
systemctl list-timers "${TIMERS[@]}" --all --no-pager || true
