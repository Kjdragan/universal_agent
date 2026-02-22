#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/universal_agent

PY_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PY_BIN=".venv/bin/python"
fi

export PYTHONPATH="src:${PYTHONPATH:-}"
exec "$PY_BIN" scripts/youtube_playlist_poll_to_manual_hook.py "$@"
