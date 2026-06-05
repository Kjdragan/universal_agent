#!/usr/bin/env bash
# Install + enable the S5 Phase A batch-1 deterministic systemd timers.
# Idempotent — safe to re-run on every deploy. Mirrors
# scripts/install_uv_cache_prune_timer.sh / install_vps_proactive_health_timer.sh.
#
# These 5 slot-critical maintenance/audit jobs were migrated OFF the in-process
# gateway cron (which loses 17-49% of fires to the ~19 daily deploy restarts)
# onto deploy-INDEPENDENT OnCalendar+Persistent timers. The OS replays a slot
# missed inside a deploy window, and the daemon-reload each deploy performs
# re-arms them (OnCalendar+Persistent survive it; a monotonic timer would not).
#
# Double-fire is prevented on the OTHER side: gateway_server._is_migrated_to_systemd
# forces each job's in-process cron registration disabled, so the timer is the
# SOLE firer. ROLLBACK: UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1 + restart gateway
# re-enables in-process firing; then `systemctl disable --now <timer>` here.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"

# system_job -> unit basename (underscores in the job name become dashes in the
# unit). The ExecStart module inside each .service is the real `!script` module
# (NOT the system_job name) — verified against the cron registry.
UNITS=(
  "universal-agent-scratch-pruning"
  "universal-agent-vault-lint-contradictions"
  "universal-agent-architecture-canvas-drift"
  "universal-agent-insight-scoring-health"
  "universal-agent-vp-coder-workspace-pruning"
)

# Validate every unit file is present BEFORE touching systemd.
for base in "${UNITS[@]}"; do
  for ext in service timer; do
    src="$APP_ROOT/deployment/systemd/$base.$ext"
    if [[ ! -f "$src" ]]; then
      echo "Missing required unit file: $src" >&2
      exit 2
    fi
  done
done

# Install all unit files, then a single daemon-reload.
for base in "${UNITS[@]}"; do
  for ext in service timer; do
    install -m 0644 "$APP_ROOT/deployment/systemd/$base.$ext" "$SYSTEMD_DIR/$base.$ext"
  done
done

systemctl daemon-reload

# enable --now starts the TIMER (not the oneshot service). We deliberately do
# NOT `systemctl start` the services at install — these are scheduled
# maintenance jobs with no need to fire immediately; the timer arms them.
for base in "${UNITS[@]}"; do
  systemctl enable --now "$base.timer"
done

echo "== Phase A batch-1 timers =="
systemctl list-timers "${UNITS[0]}.timer" "${UNITS[1]}.timer" "${UNITS[2]}.timer" \
  "${UNITS[3]}.timer" "${UNITS[4]}.timer" --all --no-pager || true
