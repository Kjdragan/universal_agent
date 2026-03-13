#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET_STAGE="${TARGET_STAGE:-${1:-}}"
DEPLOY_PROFILE="${DEPLOY_PROFILE:-local_workstation}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env}"
WEBUI_ENV_FILE="${WEBUI_ENV_FILE:-$APP_ROOT/web-ui/.env.local}"
MACHINE_SLUG="${UA_MACHINE_SLUG:-kevins-desktop}"
FACTORY_ROLE_VALUE="${FACTORY_ROLE_VALUE:-LOCAL_WORKER}"
SERVICE_CONTROL_SCRIPT="${SERVICE_CONTROL_SCRIPT:-$APP_ROOT/scripts/control_local_factory_service.sh}"
PY_BIN="${PY_BIN:-$APP_ROOT/.venv/bin/python}"

usage() {
  cat <<EOF
Usage: $0 --stage <staging|production>

Bootstraps the current checkout as a desktop LOCAL_WORKER lane for a deployed stage.

Environment variables respected:
  TARGET_STAGE          Stage to target (staging|production)
  DEPLOY_PROFILE        Deployment profile override (default: local_workstation)
  UA_MACHINE_SLUG       Machine slug written to bootstrap env (default: kevins-desktop)
  FACTORY_ROLE_VALUE    Factory role written to bootstrap env (default: LOCAL_WORKER)
  INFISICAL_CLIENT_ID
  INFISICAL_CLIENT_SECRET
  INFISICAL_PROJECT_ID
  INFISICAL_API_URL
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)
      TARGET_STAGE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

TARGET_STAGE="$(printf '%s' "${TARGET_STAGE:-}" | tr '[:upper:]' '[:lower:]')"
if [[ "$TARGET_STAGE" != "staging" && "$TARGET_STAGE" != "production" ]]; then
  echo "Target stage must be 'staging' or 'production'." >&2
  usage
  exit 2
fi

if [[ ! -x "$PY_BIN" ]]; then
  echo "Python runtime not found at $PY_BIN" >&2
  exit 3
fi

resolve_from_env_file() {
  local key="$1"
  if [[ -n "${!key:-}" ]]; then
    printf '%s' "${!key}"
    return 0
  fi
  if [[ ! -f "$ENV_FILE" ]]; then
    return 1
  fi
  python3 - "$ENV_FILE" "$key" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
target = sys.argv[2]
if not env_path.exists():
    raise SystemExit(1)
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == target:
        print(value.strip().strip('"').strip("'"))
        raise SystemExit(0)
raise SystemExit(1)
PY
}

INFISICAL_CLIENT_ID="$(resolve_from_env_file INFISICAL_CLIENT_ID || true)"
INFISICAL_CLIENT_SECRET="$(resolve_from_env_file INFISICAL_CLIENT_SECRET || true)"
INFISICAL_PROJECT_ID="$(resolve_from_env_file INFISICAL_PROJECT_ID || true)"
INFISICAL_API_URL="$(resolve_from_env_file INFISICAL_API_URL || true)"

missing=()
[[ -n "$INFISICAL_CLIENT_ID" ]] || missing+=("INFISICAL_CLIENT_ID")
[[ -n "$INFISICAL_CLIENT_SECRET" ]] || missing+=("INFISICAL_CLIENT_SECRET")
[[ -n "$INFISICAL_PROJECT_ID" ]] || missing+=("INFISICAL_PROJECT_ID")
if (( ${#missing[@]} > 0 )); then
  echo "Missing required bootstrap credentials: ${missing[*]}" >&2
  exit 4
fi

tmp_env="$(mktemp "${TMPDIR:-/tmp}/ua-local-worker-env.XXXXXX")"
trap 'rm -f "$tmp_env"' EXIT

emit_env_line() {
  local key="$1"
  local value="$2"
  python3 - "$key" "$value" <<'PY'
import shlex
import sys

key, value = sys.argv[1], sys.argv[2]
print(f"{key}={shlex.quote(value)}")
PY
}

{
  emit_env_line "INFISICAL_CLIENT_ID" "$INFISICAL_CLIENT_ID"
  emit_env_line "INFISICAL_CLIENT_SECRET" "$INFISICAL_CLIENT_SECRET"
  emit_env_line "INFISICAL_PROJECT_ID" "$INFISICAL_PROJECT_ID"
  if [[ -n "$INFISICAL_API_URL" ]]; then
    emit_env_line "INFISICAL_API_URL" "$INFISICAL_API_URL"
  fi
  emit_env_line "INFISICAL_ENVIRONMENT" "$TARGET_STAGE"
  emit_env_line "UA_RUNTIME_STAGE" "$TARGET_STAGE"
  emit_env_line "FACTORY_ROLE" "$FACTORY_ROLE_VALUE"
  emit_env_line "UA_DEPLOYMENT_PROFILE" "$DEPLOY_PROFILE"
  emit_env_line "UA_MACHINE_SLUG" "$MACHINE_SLUG"
} > "$tmp_env"

install -m 600 "$tmp_env" "$ENV_FILE"
echo "Installed local worker bootstrap env to $ENV_FILE"

if [[ -x "$APP_ROOT/scripts/install_local_webui_env.sh" ]]; then
  APP_ROOT="$APP_ROOT" DEPLOY_PROFILE="$DEPLOY_PROFILE" WEBUI_ENV_FILE="$WEBUI_ENV_FILE" \
    bash "$APP_ROOT/scripts/install_local_webui_env.sh"
fi

if [[ -x "$SERVICE_CONTROL_SCRIPT" ]]; then
  "$SERVICE_CONTROL_SCRIPT" start || true
fi

env \
  INFISICAL_CLIENT_ID="$INFISICAL_CLIENT_ID" \
  INFISICAL_CLIENT_SECRET="$INFISICAL_CLIENT_SECRET" \
  INFISICAL_PROJECT_ID="$INFISICAL_PROJECT_ID" \
  INFISICAL_API_URL="$INFISICAL_API_URL" \
  INFISICAL_ENVIRONMENT="$TARGET_STAGE" \
  UA_RUNTIME_STAGE="$TARGET_STAGE" \
  FACTORY_ROLE="$FACTORY_ROLE_VALUE" \
  UA_DEPLOYMENT_PROFILE="$DEPLOY_PROFILE" \
  UA_MACHINE_SLUG="$MACHINE_SLUG" \
  PYTHONPATH="$APP_ROOT/src" \
  "$PY_BIN" - <<'PY'
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.runtime_role import resolve_factory_role, resolve_machine_slug, resolve_runtime_stage

initialize_runtime_secrets(profile="local_workstation", force_reload=True)
print(f"LOCAL_WORKER_ROLE={resolve_factory_role().value}")
print(f"LOCAL_WORKER_STAGE={resolve_runtime_stage()}")
print(f"LOCAL_WORKER_MACHINE={resolve_machine_slug()}")
PY
