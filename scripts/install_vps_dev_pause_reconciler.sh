#!/usr/bin/env bash
# ==============================================================================
# scripts/install_vps_dev_pause_reconciler.sh
# ------------------------------------------------------------------------------
# One-time install of a safety-net systemd timer on the VPS. It reads the
# dev-mode pause stamp written by scripts/dev_up.sh, and when the stamp's
# `expires_at_epoch` has passed (i.e. Kevin forgot to run dev_down.sh), it
# auto-releases the pause and restarts the services so the VPS does not stay
# degraded forever.
#
# Usage (from local laptop):
#   ssh -i ~/.ssh/id_ed25519 root@uaonvps 'bash -s' \
#       < scripts/install_vps_dev_pause_reconciler.sh
#
# Or copy and run on the VPS as root. Requires: systemd, bash.
#
# Idempotent: re-running just rewrites the unit files and re-enables the timer.
# ==============================================================================
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This installer must run as root on the VPS." >&2
  exit 1
fi

PAUSE_STAMP_PATH="${UA_VPS_PAUSE_STAMP_PATH:-/etc/universal-agent/dev_pause.stamp}"
RECONCILER_BIN="/usr/local/sbin/ua-dev-pause-reconciler.sh"
SERVICE_UNIT="/etc/systemd/system/ua-dev-pause-reconciler.service"
TIMER_UNIT="/etc/systemd/system/ua-dev-pause-reconciler.timer"

# Must match the unit list in scripts/dev_up.sh / dev_down.sh.
CONFLICT_SERVICES=(
  "universal-agent-api.service"
  "universal-agent-gateway.service"
  "universal-agent-webui.service"
  "universal-agent-telegram.service"
  "ua-discord-cc-bot.service"
  "universal-agent-service-watchdog.service"
)
CONFLICT_TIMERS=(
  "universal-agent-service-watchdog.timer"
  "universal-agent-youtube-playlist-poller.timer"
)

mkdir -p "$(dirname "$PAUSE_STAMP_PATH")"

# ------------------------------------------------------------------------------
# Reconciler binary: reads stamp, checks expiry, restarts if needed.
# ------------------------------------------------------------------------------
cat > "$RECONCILER_BIN" <<RECONCILER
#!/usr/bin/env bash
set -Eeuo pipefail

PAUSE_STAMP_PATH="$PAUSE_STAMP_PATH"
CONFLICT_SERVICES=(${CONFLICT_SERVICES[@]@Q})
CONFLICT_TIMERS=(${CONFLICT_TIMERS[@]@Q})

log() { printf '[ua-dev-pause-reconciler] %s\n' "\$*"; }

if [[ ! -f "\$PAUSE_STAMP_PATH" ]]; then
  log "no pause stamp present; nothing to do"
  exit 0
fi

expires_at_epoch=""
while IFS='=' read -r key value; do
  case "\$key" in
    expires_at_epoch) expires_at_epoch="\$value" ;;
  esac
done < "\$PAUSE_STAMP_PATH"

if [[ -z "\$expires_at_epoch" ]]; then
  log "pause stamp has no expires_at_epoch; leaving as-is"
  exit 0
fi

now="\$(date +%s)"
if (( now < expires_at_epoch )); then
  log "pause stamp still valid (\$((expires_at_epoch - now))s remaining); leaving as-is"
  exit 0
fi

log "pause stamp expired; auto-releasing and restarting services"
rm -f "\$PAUSE_STAMP_PATH"

for svc in "\${CONFLICT_SERVICES[@]}"; do
  systemctl start "\$svc" || log "failed to start \$svc"
done
for tmr in "\${CONFLICT_TIMERS[@]}"; do
  systemctl start "\$tmr" || log "failed to start \$tmr"
done

log "auto-release complete"
RECONCILER
chmod 0755 "$RECONCILER_BIN"

# ------------------------------------------------------------------------------
# systemd oneshot service
# ------------------------------------------------------------------------------
cat > "$SERVICE_UNIT" <<'SERVICE'
[Unit]
Description=Universal Agent dev-mode pause reconciler (auto-release expired pauses)
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ua-dev-pause-reconciler.sh
SERVICE

# ------------------------------------------------------------------------------
# systemd timer
# ------------------------------------------------------------------------------
cat > "$TIMER_UNIT" <<'TIMER'
[Unit]
Description=Run the Universal Agent dev-mode pause reconciler every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Unit=ua-dev-pause-reconciler.service

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
systemctl enable --now ua-dev-pause-reconciler.timer

echo "Installed:"
echo "  $RECONCILER_BIN"
echo "  $SERVICE_UNIT"
echo "  $TIMER_UNIT"
echo
systemctl status --no-pager ua-dev-pause-reconciler.timer || true
