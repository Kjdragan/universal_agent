#!/usr/bin/env bash
# Install the desktop SSHFS bridge as NATIVE systemd units and evict any
# /etc/fstab entry for it. Idempotent — safe to re-run on every deploy.
#
# WHY THIS EXISTS (root cause of the recurring exit-124 deploy timeout):
#   systemd-fstab-generator runs on EVERY `systemctl daemon-reload`, and for
#   each fstab entry it canonicalizes the mount point — chase() -> openat() ->
#   statx(). When the /home/kjdragan SSHFS mount is WEDGED (desktop asleep or
#   unreachable) that statx() blocks in uninterruptible D state. The generator
#   never exits, systemd's generator sandbox is killed by alarm(90)
#   (DEFAULT_TIMEOUT_USEC), the child exits non-zero, and PID 1 logs
#     "Failed to fork off sandboxing environment for executing generators:
#      Protocol error"
#   Every daemon-reload then costs a hard 90.3 s instead of ~0.5 s. A deploy
#   performs ~63 reloads, so the installer block alone runs ~95 min and the
#   workflow's `timeout 30m ssh` kills it with exit 124.
#
#   Measured on uaonvps 2026-08-02 with the mount deliberately wedged
#   (SIGSTOP on the sshfs daemon, fuse connection waiting>0):
#     bridge in /etc/fstab   -> daemon-reload 90.42 s  (+ EPROTO in journal)
#     bridge as native units -> daemon-reload  0.41 s  (0 EPROTO)
#   The mount is still present in the namespace in both cases. What changes is
#   only whether a GENERATOR is told to walk into it.
#
# ORDER BELOW IS LOAD-BEARING: install units FIRST, strip fstab SECOND, reload
# THIRD. Stripping fstab while the only unit fragment backing the live mount is
# generator output would make the next reload flush that fragment and leave
# systemd with no unit under a mounted filesystem — the exact
# "Suppressing automount event since unit is no longer loaded" /
# Result=resources state seen on this box on 07-31 and 08-01.
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
SYSTEMD_DIR="/etc/systemd/system"
MOUNT_NAME="home-kjdragan.mount"
AUTOMOUNT_NAME="home-kjdragan.automount"
BRIDGE_PATH="/home/kjdragan"

# Peer identity is templated so the operator's machine coordinates are config,
# not hardcoded repo content, and so a rebuild can point at a different desktop.
BRIDGE_USER="${UA_BRIDGE_USER:-kjdragan}"
BRIDGE_PEER="${UA_BRIDGE_PEER:-100.95.187.38}"
BRIDGE_IDENTITY="${UA_BRIDGE_IDENTITY:-/root/.ssh/id_ed25519}"

changed=0

render_and_install() {
  # Render the template, then compare the RENDERED text against what is
  # installed. Comparing the template itself would always differ and defeat the
  # compare-and-skip (the trap already present in install_vps_systemd_units.sh).
  local name="$1"
  local src="$APP_ROOT/deployment/systemd/$name"
  local dst="$SYSTEMD_DIR/$name"
  local tmp

  if [[ ! -f "$src" ]]; then
    echo "Missing required file: $src" >&2
    exit 2
  fi

  tmp="$(mktemp)"
  sed -e "s|__BRIDGE_USER__|$BRIDGE_USER|g" \
      -e "s|__BRIDGE_PEER__|$BRIDGE_PEER|g" \
      -e "s|__BRIDGE_IDENTITY__|$BRIDGE_IDENTITY|g" \
      "$src" > "$tmp"

  if [[ -f "$dst" ]] && cmp -s "$tmp" "$dst"; then
    echo "  $name unchanged"
    rm -f "$tmp"
    return 0
  fi

  install -m 0644 "$tmp" "$dst"
  rm -f "$tmp"
  echo "  $name installed"
  changed=1
}

echo "--> Desktop bridge: installing native systemd units"
render_and_install "$MOUNT_NAME"
render_and_install "$AUTOMOUNT_NAME"

# ---------------------------------------------------------------------------
# Evict the bridge from /etc/fstab. This is THE fix — see the header. The guard
# is on the CONTENTS of /etc/fstab, not on any one writer, because a single
# stray `>> /etc/fstab` anywhere silently restores the 90 s stall.
# ---------------------------------------------------------------------------
if grep -qE "^[^#].*[[:space:]]${BRIDGE_PATH}[[:space:]]" /etc/fstab 2>/dev/null; then
  backup="/root/fstab.bak.$(date -u +%Y%m%dT%H%M%SZ)"
  cp -a /etc/fstab "$backup"
  # Match on the MOUNT POINT field, not the fs type: this must also catch a
  # hand-restored line whose options drifted.
  sed -i -E "\%^[^#].*[[:space:]]${BRIDGE_PATH}[[:space:]]%d" /etc/fstab
  echo "  EVICTED $BRIDGE_PATH from /etc/fstab (backup: $backup)"
  echo "  (an fstab entry here costs every daemon-reload a hard 90s when the bridge is wedged)"
  changed=1
else
  echo "  /etc/fstab clean (no $BRIDGE_PATH entry) — as required"
fi

if [[ "$changed" -eq 1 ]]; then
  # Safe by construction: /etc/fstab no longer names the bridge, so this reload
  # cannot stall even if the mount is wedged right now.
  systemctl daemon-reload
else
  echo "  nothing changed — skipping daemon-reload"
fi

# Enable the AUTOMOUNT, never `--now` the .mount: starting the mount here would
# attempt a real SSHFS connection mid-deploy and block TimeoutSec against a
# desktop that may be asleep. The automount arms autofs without connecting.
systemctl enable "$AUTOMOUNT_NAME" >/dev/null 2>&1 || true
if ! systemctl is-active --quiet "$AUTOMOUNT_NAME"; then
  systemctl reset-failed "$AUTOMOUNT_NAME" "$MOUNT_NAME" 2>/dev/null || true
  systemctl start "$AUTOMOUNT_NAME" || echo "  WARN: could not start $AUTOMOUNT_NAME"
fi

echo "  $AUTOMOUNT_NAME: $(systemctl is-active "$AUTOMOUNT_NAME" 2>/dev/null || echo unknown)"
echo "  generator fragments for bridge: $(ls /run/systemd/generator/ 2>/dev/null | grep -c home-kjdragan || true) (must be 0)"
