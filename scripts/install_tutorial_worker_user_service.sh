#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_UNIT="$REPO_ROOT/deployment/systemd-user/universal-agent-tutorial-worker.service"
DEST_DIR="${HOME}/.config/systemd/user"
DEST_UNIT="${DEST_DIR}/universal-agent-tutorial-worker.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "ERROR: systemctl not found. Cannot install systemd user unit."
  exit 1
fi

if [ ! -f "$SRC_UNIT" ]; then
  echo "ERROR: unit template not found: $SRC_UNIT"
  exit 1
fi

if [ ! -f "$REPO_ROOT/scripts/start_tutorial_local_worker.sh" ]; then
  echo "ERROR: missing worker start script: $REPO_ROOT/scripts/start_tutorial_local_worker.sh"
  exit 1
fi

mkdir -p "$DEST_DIR"
sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$SRC_UNIT" >"$DEST_UNIT"

echo "Installed: $DEST_UNIT"

systemctl --user daemon-reload
systemctl --user enable --now universal-agent-tutorial-worker.service
systemctl --user --no-pager --full status universal-agent-tutorial-worker.service || true

echo
echo "If you need user services to start at boot without login:"
echo "  sudo loginctl enable-linger $(whoami)"
