#!/usr/bin/env bash
# Install + enable the desktop->VPS migrated cron timers.
#
# These two jobs previously ran ONLY as `systemctl --user` timers on Kevin's
# desktop (mint-desktop) — a violation of the desktop=dev / VPS=runtime contract
# (see CLAUDE.md "Runtime vs Development Environment Contract"). They are migrated
# here to canonical VPS system units (root-installed under /etc/systemd/system,
# [Service] User=ua), matching the briefings/digest convention.
#
#   universal-agent-backlog-triage   — daily 08:30 CT, Simone backlog email + reminders
#   universal-agent-skill-gap-finder — every 5 days 09:00 CT, transcript mining -> issue/email
#
# Unlike the batch-A* timers, NEITHER job has an in-process gateway twin
# (neither backlog_triage nor skill_gap_finder is registered in
# gateway_server.py), so there is NO _is_migrated_to_systemd double-fire gate to
# coordinate. Both modules self-bootstrap secrets via initialize_runtime_secrets().
#
# Idempotent — safe to re-run on every deploy. Kept as a separate installer (not
# folded into batch-A4) so the migration is self-contained and trivially
# reviewable/reversible. ROLLBACK: `systemctl disable --now <timer>` for each.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
SRC="$APP_ROOT/deployment/systemd"

SERVICES=(
  "universal-agent-backlog-triage.service"
  "universal-agent-skill-gap-finder.service"
)
TIMERS=(
  "universal-agent-backlog-triage.timer"
  "universal-agent-skill-gap-finder.timer"
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

# enable --now arms the TIMERS (the oneshot services fire on their normal slots;
# we do NOT `systemctl start` them at install — they send operator email).
for t in "${TIMERS[@]}"; do
  systemctl enable --now "$t"
done

echo "== Desktop->VPS migrated timers =="
systemctl list-timers "${TIMERS[@]}" --all --no-pager || true
