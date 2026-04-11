#!/usr/bin/env bash
# ==============================================================================
# DEPRECATED — DO NOT USE
# ------------------------------------------------------------------------------
# This script writes Infisical credentials to a plaintext .env file, which
# violates the project's "no secrets on disk" rule. It is retained only so
# existing references do not 404; it will be removed in a future cleanup.
#
# Use instead:   scripts/dev_up.sh
# Canonical doc: docs/development/LOCAL_DEV.md
#
# If you genuinely need to run this (e.g. to compare against the new flow),
# set I_KNOW_THIS_IS_DEPRECATED=1 explicitly.
# ==============================================================================
if [[ "${I_KNOW_THIS_IS_DEPRECATED:-0}" != "1" ]]; then
  cat >&2 <<'DEPRECATED'
bootstrap_local_hq_dev.sh is DEPRECATED.

It writes Infisical credentials to a plaintext .env file, which violates the
project's "no secrets on disk" rule.

Use the new local dev workflow instead:

  ./scripts/dev_up.sh      # start local stack (infisical run, secrets in memory)
  ./scripts/dev_status.sh  # read-only health check
  ./scripts/dev_down.sh    # stop local stack, resume VPS

Canonical guide: docs/development/LOCAL_DEV.md

To bypass this hard stop anyway:  I_KNOW_THIS_IS_DEPRECATED=1 ./scripts/bootstrap_local_hq_dev.sh
DEPRECATED
  exit 2
fi

set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET_ENV="${TARGET_ENV:-development}"
DEPLOY_PROFILE="${DEPLOY_PROFILE:-local_workstation}"
RUNTIME_STAGE="${RUNTIME_STAGE:-development}"
MACHINE_SLUG="${UA_MACHINE_SLUG:-kevins-desktop}"
FACTORY_ROLE_VALUE="${FACTORY_ROLE_VALUE:-HEADQUARTERS}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env}"
WEBUI_ENV_FILE="${WEBUI_ENV_FILE:-$APP_ROOT/web-ui/.env.local}"
WORKER_UNIT="${WORKER_UNIT:-universal-agent-local-factory.service}"
PY_BIN="${PY_BIN:-$APP_ROOT/.venv/bin/python}"

usage() {
  cat <<EOF
Usage: $0 [target-env]

Bootstraps the repo checkout as a local HEADQUARTERS development lane.

Environment variables respected:
  TARGET_ENV            Infisical environment slug (default: development)
  DEPLOY_PROFILE        Deployment profile override (default: local_workstation)
  RUNTIME_STAGE         Runtime stage written to bootstrap env (default: development)
  FACTORY_ROLE_VALUE    Factory role written to bootstrap env (default: HEADQUARTERS)
  UA_MACHINE_SLUG       Machine slug written to bootstrap env (default: kevins-desktop)
  INFISICAL_CLIENT_ID
  INFISICAL_CLIENT_SECRET
  INFISICAL_PROJECT_ID
  INFISICAL_API_URL
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 0 && -n "${1:-}" ]]; then
  TARGET_ENV="$1"
fi

if [[ ! -x "$PY_BIN" ]]; then
  echo "Python runtime not found at $PY_BIN" >&2
  exit 2
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
  exit 3
fi

tmp_env="$(mktemp "${TMPDIR:-/tmp}/ua-hq-dev-env.XXXXXX")"
backup_env=""
if [[ -f "$ENV_FILE" ]]; then
  backup_env="$(mktemp "${TMPDIR:-/tmp}/ua-prev-env.XXXXXX")"
  cp "$ENV_FILE" "$backup_env"
fi

restore_original_env() {
  local exit_code=$?
  rm -f "$tmp_env"
  if [[ $exit_code -ne 0 && -n "$backup_env" && -f "$backup_env" ]]; then
    install -m 600 "$backup_env" "$ENV_FILE"
  fi
  if [[ -n "$backup_env" && -f "$backup_env" ]]; then
    rm -f "$backup_env"
  fi
  exit $exit_code
}
trap restore_original_env EXIT

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
  emit_env_line "INFISICAL_ENVIRONMENT" "$TARGET_ENV"
  emit_env_line "UA_RUNTIME_STAGE" "$RUNTIME_STAGE"
  emit_env_line "FACTORY_ROLE" "$FACTORY_ROLE_VALUE"
  emit_env_line "UA_DEPLOYMENT_PROFILE" "$DEPLOY_PROFILE"
  emit_env_line "UA_MACHINE_SLUG" "$MACHINE_SLUG"
} > "$tmp_env"

install -m 600 "$tmp_env" "$ENV_FILE"
echo "Installed HQ dev bootstrap env to $ENV_FILE"

APP_ROOT="$APP_ROOT" DEPLOY_PROFILE="$DEPLOY_PROFILE" WEBUI_ENV_FILE="$WEBUI_ENV_FILE" \
  bash "$APP_ROOT/scripts/install_local_webui_env.sh"

env \
  INFISICAL_CLIENT_ID="$INFISICAL_CLIENT_ID" \
  INFISICAL_CLIENT_SECRET="$INFISICAL_CLIENT_SECRET" \
  INFISICAL_PROJECT_ID="$INFISICAL_PROJECT_ID" \
  INFISICAL_API_URL="$INFISICAL_API_URL" \
  INFISICAL_ENVIRONMENT="$TARGET_ENV" \
  UA_RUNTIME_STAGE="$RUNTIME_STAGE" \
  FACTORY_ROLE="$FACTORY_ROLE_VALUE" \
  UA_DEPLOYMENT_PROFILE="$DEPLOY_PROFILE" \
  UA_MACHINE_SLUG="$MACHINE_SLUG" \
  PYTHONPATH="$APP_ROOT/src" \
  "$PY_BIN" - <<'PY'
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.runtime_role import resolve_factory_role, resolve_runtime_stage, resolve_machine_slug
import os

requested_profile = str(os.getenv("UA_DEPLOYMENT_PROFILE") or "").strip()
initialize_runtime_secrets(profile=os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation", force_reload=True)
role = resolve_factory_role().value
runtime_stage = resolve_runtime_stage()
machine_slug = resolve_machine_slug()
print(f"HQ_DEV_ROLE={role}")
print(f"HQ_DEV_REQUESTED_PROFILE={requested_profile}")
print(f"HQ_DEV_RUNTIME_STAGE={runtime_stage}")
print(f"HQ_DEV_MACHINE_SLUG={machine_slug}")
if (
    role != "HEADQUARTERS"
    or requested_profile != "local_workstation"
    or runtime_stage != "development"
    or machine_slug != "kevins-desktop"
):
    raise SystemExit("HQ dev bootstrap verification failed")
PY

if systemctl --user is-active --quiet "$WORKER_UNIT"; then
  echo
  echo "WARNING: $WORKER_UNIT is active on this desktop."
  echo "You can Pause Intake or Stop Local Factory from /dashboard/corporation if resource pressure becomes an issue."
fi

cat <<EOF

HQ dev bootstrap complete.

Run commands:
  Gateway:
    cd "$APP_ROOT" && set -a && source .env && set +a && PYTHONPATH=src "$PY_BIN" -m universal_agent.gateway_server

  API:
    cd "$APP_ROOT" && set -a && source .env && set +a && PYTHONPATH=src "$PY_BIN" -m universal_agent.api.server

  Web UI:
    cd "$APP_ROOT/web-ui" && npm run dev

Expected local URLs:
  http://localhost:3000
  http://localhost:3000/dashboard/corporation
EOF
