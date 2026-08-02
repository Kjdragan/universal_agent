#!/usr/bin/env bash
# Detect and recover a WEDGED desktop SSHFS bridge (/home/kjdragan) on the VPS.
#
# Keeping the bridge alive is the point: it is a documented capability (agents
# resolve /home/kjdragan/... on the VPS through it). With TimeoutIdleSec=0
# nothing ever re-triggers a fresh connection, so without this watchdog a wedge
# persists for every consumer until sshfs's `reconnect` happens to win.
#
# CRITICAL — the two rules that make this correct, both established by
# measurement on uaonvps 2026-08-02:
#
#  1. NEVER stat/ls/test the bridge path to probe health. A hung FUSE call is
#     UNINTERRUPTIBLE: the caller parks in D state at request_wait_answer and
#     `timeout N stat` does NOT bound it (timeout itself then blocks in
#     sigsuspend waiting to reap a child that cannot die; SIGKILL is ignored).
#     A probe like `timeout 5 stat -t /home/kjdragan/.` hangs the caller HARDER
#     than the bug it guards. We read /sys/fs/fuse/connections/<minor>/waiting
#     instead — a pure kernel counter, no FUSE round trip.
#     (Second trap: with the automount dead, the path is an empty LOCAL dir that
#     stats in 2 ms and looks perfectly healthy. stat cannot tell the three
#     states — healthy / wedged / absent — apart at all.)
#
#  2. The ONLY primitive that frees D-state waiters is aborting the FUSE
#     connection: `echo 1 > /sys/fs/fuse/connections/<minor>/abort`.
#       - `pkill -f mount.fuse.sshfs` does NOT kill a wedged sshfs daemon (it
#         survives in futex_do_wait),
#       - `umount -l` / `fusermount -uz` remove the mountinfo entry but leave
#         waiting>0, the connection dir present, and the process in D.
#     Both of those were the twice-tried manual "fix" on 07-31 and 08-01. They
#     left a non-empty unit cgroup ("Found left-over process (mount.fuse.sshf)
#     in control group"), so systemd's next start failed with Result=resources
#     and the bridge stayed DOWN instead of recovering. That is why "just
#     unmount it" failed twice.
set -Eeuo pipefail

BRIDGE_PATH="${UA_BRIDGE_PATH:-/home/kjdragan}"
MOUNT_UNIT="home-kjdragan.mount"
AUTOMOUNT_UNIT="home-kjdragan.automount"
SAMPLES="${UA_BRIDGE_SAMPLES:-6}"
INTERVAL="${UA_BRIDGE_SAMPLE_INTERVAL:-10}"

log() { echo "[bridge-watchdog] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

fuse_minor() {
  # mountinfo field 3 is maj:min, field 5 is the mount point. No FUSE I/O.
  awk -v p="$BRIDGE_PATH" '$5 == p && $0 ~ /fuse\.sshfs/ { split($3, a, ":"); print a[2]; exit }' \
    /proc/self/mountinfo
}

remediate() {
  local minor="$1" reason="$2"
  log "WEDGED ($reason) — remediating"
  if [[ -n "$minor" && -w "/sys/fs/fuse/connections/$minor/abort" ]]; then
    # Frees every D-state waiter immediately; they become reapable.
    echo 1 > "/sys/fs/fuse/connections/$minor/abort" || true
    log "aborted fuse connection minor=$minor"
    sleep 2
  fi
  # LazyUnmount/ForceUnmount on the .mount unit let systemd detach cleanly now
  # that the connection is aborted, so the cgroup actually empties.
  systemctl stop "$MOUNT_UNIT" 2>/dev/null || true
  systemctl reset-failed "$MOUNT_UNIT" "$AUTOMOUNT_UNIT" 2>/dev/null || true
  # Re-ARM the automount. We deliberately do NOT mount here: the next consumer
  # access triggers a fresh connection. Never leave the automount stopped —
  # that silently removes the documented Path Guarantee.
  systemctl start "$AUTOMOUNT_UNIT" 2>/dev/null || log "WARN: could not re-arm $AUTOMOUNT_UNIT"
  log "automount state=$(systemctl is-active "$AUTOMOUNT_UNIT" 2>/dev/null || echo unknown)"
}

# --- Failure mode 1: units failed outright (the "bridge silently gone" case) --
for unit in "$AUTOMOUNT_UNIT" "$MOUNT_UNIT"; do
  state="$(systemctl show -p ActiveState --value "$unit" 2>/dev/null || echo unknown)"
  result="$(systemctl show -p Result --value "$unit" 2>/dev/null || echo unknown)"
  if [[ "$state" == "failed" || "$result" == "resources" ]]; then
    remediate "$(fuse_minor)" "$unit ActiveState=$state Result=$result"
    exit 0
  fi
done

# The automount being inactive means the capability is gone even though nothing
# looks "failed". Observed on 2026-08-02: dead for 1h20m, unnoticed.
if ! systemctl is-active --quiet "$AUTOMOUNT_UNIT"; then
  log "automount inactive — re-arming"
  systemctl reset-failed "$MOUNT_UNIT" "$AUTOMOUNT_UNIT" 2>/dev/null || true
  systemctl start "$AUTOMOUNT_UNIT" 2>/dev/null || log "WARN: could not re-arm"
  exit 0
fi

# --- Failure mode 2: mounted but wedged -------------------------------------
minor="$(fuse_minor)"
if [[ -z "$minor" ]]; then
  log "ok (automount armed, not currently mounted)"
  exit 0
fi

waiting_file="/sys/fs/fuse/connections/$minor/waiting"
[[ -r "$waiting_file" ]] || { log "ok (no waiting counter for minor=$minor)"; exit 0; }

# Require SUSTAINED waiting>0. A healthy mount drains in milliseconds, so
# ${SAMPLES} consecutive non-zero samples is a very strong signal. This matters:
# remediation aborts a live FUSE connection, so a false positive has real cost.
for _ in $(seq 1 "$SAMPLES"); do
  w="$(cat "$waiting_file" 2>/dev/null || echo 0)"
  if [[ "${w:-0}" -eq 0 ]]; then
    log "ok (minor=$minor drained)"
    exit 0
  fi
  sleep "$INTERVAL"
done

remediate "$minor" "waiting>0 sustained across ${SAMPLES}x${INTERVAL}s"
