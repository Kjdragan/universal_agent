#!/usr/bin/env bash
set -euo pipefail

# Installs and enables the systemd *user* unit that keeps the reverse SSH tunnel
# alive and auto-reconnects (Restart=always).
#
# This installs to:
#   ~/.config/systemd/user/ua-youtube-forward-tunnel.service
#
# Then enables + starts it:
#   systemctl --user enable --now ua-youtube-forward-tunnel.service

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_UNIT="$REPO_ROOT/deployment/systemd-user/ua-youtube-forward-tunnel.service"
DEST_DIR="${HOME}/.config/systemd/user"
DEST_UNIT="${DEST_DIR}/ua-youtube-forward-tunnel.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "ERROR: systemctl not found. Cannot install systemd user unit."
  exit 1
fi

if [ ! -f "$SRC_UNIT" ]; then
  echo "ERROR: unit template not found: $SRC_UNIT"
  exit 1
fi

mkdir -p "$DEST_DIR"

# Render unit with correct repo root path (portable across clones).
sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$SRC_UNIT" >"$DEST_UNIT"

echo "Installed: $DEST_UNIT"

systemctl --user daemon-reload
systemctl --user enable --now ua-youtube-forward-tunnel.service
systemctl --user --no-pager --full status ua-youtube-forward-tunnel.service || true

echo
echo "Note: systemd user services start when you log in."
echo "If you need it to start at boot even without login, consider:"
echo "  sudo loginctl enable-linger $(whoami)"

