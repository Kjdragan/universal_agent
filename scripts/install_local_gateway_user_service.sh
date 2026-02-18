#!/usr/bin/env bash
set -euo pipefail

# Installs a systemd user service to keep the local UA gateway running on :8002.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_UNIT="$REPO_ROOT/deployment/systemd-user/universal-agent-local-gateway.service"
DEST_DIR="${HOME}/.config/systemd/user"
DEST_UNIT="${DEST_DIR}/universal-agent-local-gateway.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "ERROR: systemctl not found. Cannot install systemd user unit."
  exit 1
fi

if [ ! -f "$SRC_UNIT" ]; then
  echo "ERROR: unit template not found: $SRC_UNIT"
  exit 1
fi

if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
  echo "ERROR: Python interpreter missing at $REPO_ROOT/.venv/bin/python"
  echo "Create the virtualenv first, then rerun."
  exit 1
fi

mkdir -p "$DEST_DIR"
sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$SRC_UNIT" >"$DEST_UNIT"

echo "Installed: $DEST_UNIT"
systemctl --user daemon-reload
systemctl --user enable --now universal-agent-local-gateway.service
systemctl --user --no-pager --full status universal-agent-local-gateway.service || true

echo
echo "If you need it to start at boot without login:"
echo "  sudo loginctl enable-linger $(whoami)"
