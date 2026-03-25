#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/universal_agent

_ua_is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

_ua_is_falsy() {
  case "${1,,}" in
    0|false|no|off) return 0 ;;
    *) return 1 ;;
  esac
}

_watcher_raw="${UA_YT_PLAYLIST_WATCHER_ENABLED:-}"
_profile="${UA_DEPLOYMENT_PROFILE:-local_workstation}"
_native_watcher_enabled=0
if [[ -n "${_watcher_raw// }" ]]; then
  if ! _ua_is_falsy "${_watcher_raw}"; then
    _native_watcher_enabled=1
  fi
elif [[ "${_profile,,}" != "local_workstation" ]]; then
  _native_watcher_enabled=1
fi

if ! _ua_is_truthy "${UA_ALLOW_LEGACY_YOUTUBE_POLLER:-}" && [[ "${_native_watcher_enabled}" == "1" ]]; then
  echo "Native YouTube playlist watcher is enabled; skipping legacy poller. Set UA_ALLOW_LEGACY_YOUTUBE_POLLER=1 to force the legacy path." >&2
  exit 0
fi

PY_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PY_BIN=".venv/bin/python"
fi

export PYTHONPATH="src:${PYTHONPATH:-}"
exec "$PY_BIN" scripts/youtube_playlist_poll_to_manual_hook.py "$@"
