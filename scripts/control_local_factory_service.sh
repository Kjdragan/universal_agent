#!/usr/bin/env bash
set -Eeuo pipefail

UNIT="${UNIT:-universal-agent-local-factory.service}"
SCOPE="${SCOPE:-user}"
JSON_OUTPUT=0

usage() {
  cat <<EOF
Usage: $0 [--json] <status|start|stop>

Controls the same-machine local worker service using systemd user scope.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)
      JSON_OUTPUT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

ACTION="${1:-status}"
if [[ "$ACTION" != "status" && "$ACTION" != "start" && "$ACTION" != "stop" ]]; then
  usage >&2
  exit 2
fi

SYSTEMCTL=(systemctl)
if [[ "$SCOPE" == "user" ]]; then
  SYSTEMCTL+=(--user)
fi

state="$("${SYSTEMCTL[@]}" is-active "$UNIT" 2>/dev/null || true)"
state="${state:-unknown}"
active="false"
if [[ "$state" == "active" ]]; then
  active="true"
fi

run_action() {
  local action="$1"
  if "${SYSTEMCTL[@]}" "$action" "$UNIT"; then
    return 0
  fi
  return 1
}

ok=1
if [[ "$ACTION" == "start" || "$ACTION" == "stop" ]]; then
  if run_action "$ACTION"; then
    ok=1
  else
    ok=0
  fi
  state="$("${SYSTEMCTL[@]}" is-active "$UNIT" 2>/dev/null || true)"
  state="${state:-unknown}"
  if [[ "$state" == "active" ]]; then
    active="true"
  else
    active="false"
  fi
fi

if (( JSON_OUTPUT )); then
  python3 - "$ACTION" "$UNIT" "$SCOPE" "$state" "$active" "$ok" <<'PY'
import json
import sys

action, unit, scope, state, active, ok = sys.argv[1:]
print(json.dumps({
    "ok": ok == "1",
    "action": action,
    "unit": unit,
    "scope": scope,
    "state": state or "unknown",
    "active": active == "true",
}))
PY
  exit $(( ok == 1 ? 0 : 1 ))
fi

echo "action=$ACTION unit=$UNIT scope=$SCOPE state=$state active=$active"
exit $(( ok == 1 ? 0 : 1 ))
