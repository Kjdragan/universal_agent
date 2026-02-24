#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

SWAPFILE_PATH="${UA_SWAPFILE_PATH:-/swapfile}"
SWAPFILE_SIZE_GB="${UA_SWAPFILE_SIZE_GB:-8}"
SWAPPINESS="${UA_SWAP_SWAPPINESS:-10}"
VFS_CACHE_PRESSURE="${UA_SWAP_VFS_CACHE_PRESSURE:-50}"

if ! [[ "$SWAPFILE_SIZE_GB" =~ ^[0-9]+$ ]] || [[ "$SWAPFILE_SIZE_GB" -lt 1 ]]; then
  echo "Invalid UA_SWAPFILE_SIZE_GB=$SWAPFILE_SIZE_GB (must be integer >= 1)." >&2
  exit 2
fi

if ! [[ "$SWAPPINESS" =~ ^[0-9]+$ ]] || [[ "$SWAPPINESS" -lt 1 ]] || [[ "$SWAPPINESS" -gt 100 ]]; then
  echo "Invalid UA_SWAP_SWAPPINESS=$SWAPPINESS (must be 1-100)." >&2
  exit 2
fi

if ! [[ "$VFS_CACHE_PRESSURE" =~ ^[0-9]+$ ]] || [[ "$VFS_CACHE_PRESSURE" -lt 1 ]] || [[ "$VFS_CACHE_PRESSURE" -gt 200 ]]; then
  echo "Invalid UA_SWAP_VFS_CACHE_PRESSURE=$VFS_CACHE_PRESSURE (must be 1-200)." >&2
  exit 2
fi

if swapon --show=NAME --noheadings | grep -Fxq "$SWAPFILE_PATH"; then
  echo "Swap already active at $SWAPFILE_PATH"
else
  if [[ ! -f "$SWAPFILE_PATH" ]]; then
    echo "Creating swap file: $SWAPFILE_PATH (${SWAPFILE_SIZE_GB}G)"
    if ! fallocate -l "${SWAPFILE_SIZE_GB}G" "$SWAPFILE_PATH"; then
      echo "fallocate unavailable; falling back to dd"
      dd if=/dev/zero of="$SWAPFILE_PATH" bs=1M count="$((SWAPFILE_SIZE_GB * 1024))" status=progress
    fi
    chmod 600 "$SWAPFILE_PATH"
    mkswap "$SWAPFILE_PATH" >/dev/null
  fi
  swapon "$SWAPFILE_PATH"
fi

if ! grep -Eq "^[^#]*[[:space:]]$SWAPFILE_PATH[[:space:]]+none[[:space:]]+swap[[:space:]]" /etc/fstab; then
  echo "$SWAPFILE_PATH none swap sw 0 0" >> /etc/fstab
fi

cat >/etc/sysctl.d/99-universal-agent-swap.conf <<EOF
vm.swappiness=$SWAPPINESS
vm.vfs_cache_pressure=$VFS_CACHE_PRESSURE
EOF
sysctl --system >/dev/null

echo "== Swap status =="
swapon --show || true
echo
free -h || true
