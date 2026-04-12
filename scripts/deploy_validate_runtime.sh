#!/usr/bin/env bash
set -euo pipefail

APP_ROOT=""
EXPECT_ENVIRONMENT=""
EXPECT_RUNTIME_STAGE=""
EXPECT_FACTORY_ROLE=""
EXPECT_DEPLOYMENT_PROFILE=""
EXPECT_MACHINE_SLUG=""
PROFILE=""
SERVICE_USER="ua"
REQUIRED_KEYS=()

usage() {
  cat <<'EOF'
Usage:
  deploy_validate_runtime.sh \
    --app-root /opt/universal_agent \
    --profile vps \
    --expect-environment production \
    --expect-runtime-stage production \
    --expect-factory-role HEADQUARTERS \
    --expect-deployment-profile vps \
    --expect-machine-slug vps-hq-production \
    --require UA_OPS_TOKEN
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-root)
      APP_ROOT="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --expect-environment)
      EXPECT_ENVIRONMENT="$2"
      shift 2
      ;;
    --expect-runtime-stage)
      EXPECT_RUNTIME_STAGE="$2"
      shift 2
      ;;
    --expect-factory-role)
      EXPECT_FACTORY_ROLE="$2"
      shift 2
      ;;
    --expect-deployment-profile)
      EXPECT_DEPLOYMENT_PROFILE="$2"
      shift 2
      ;;
    --expect-machine-slug)
      EXPECT_MACHINE_SLUG="$2"
      shift 2
      ;;
    --require)
      REQUIRED_KEYS+=("$2")
      shift 2
      ;;
    --service-user)
      SERVICE_USER="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$APP_ROOT" || -z "$EXPECT_ENVIRONMENT" || -z "$EXPECT_RUNTIME_STAGE" || -z "$EXPECT_FACTORY_ROLE" || -z "$EXPECT_DEPLOYMENT_PROFILE" || -z "$EXPECT_MACHINE_SLUG" ]]; then
  usage >&2
  exit 2
fi

SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
if [[ -z "$SERVICE_HOME" ]]; then
  SERVICE_HOME="/home/$SERVICE_USER"
fi
PATH_PREFIX="$SERVICE_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

run_as_service_user() {
  local command="$1"
  if command -v sudo >/dev/null 2>&1; then
    sudo -H -u "$SERVICE_USER" env HOME="$SERVICE_HOME" /bin/bash -lc "$command"
  elif command -v runuser >/dev/null 2>&1; then
    runuser -u "$SERVICE_USER" -- env HOME="$SERVICE_HOME" /bin/bash -lc "$command"
  else
    HOME="$SERVICE_HOME" /bin/bash -lc "$command"
  fi
}

remove_runtime_venv() {
  if command -v sudo >/dev/null 2>&1; then
    sudo rm -rf "$APP_ROOT/.venv"
  else
    rm -rf "$APP_ROOT/.venv"
  fi
}

ensure_existing_venv_is_usable() {
  echo "--> Checking whether the existing venv is usable by $SERVICE_USER..."
  if [[ -e "$APP_ROOT/.venv/bin/python3" ]]; then
    if ! run_as_service_user "readlink -f \"$APP_ROOT/.venv/bin/python3\" >/dev/null 2>&1"; then
      echo "--> Existing venv interpreter is not accessible to $SERVICE_USER; removing stale .venv for clean rebuild..."
      remove_runtime_venv
    fi
  fi
}

ensure_python_runtime() {
  echo "--> Ensuring Python 3.13 runtime for uv..."
  run_as_service_user "export PATH=\"$PATH_PREFIX:\$PATH\"; uv python install 3.13"
}

sync_dependencies() {
  echo "--> Syncing dependencies with uv..."
  run_as_service_user "export PATH=\"$PATH_PREFIX:\$PATH\"; cd \"$APP_ROOT\" && uv sync --python 3.13 --no-install-package manim --no-install-package pycairo --no-install-package manimpango"
}

ensure_venv_python_executable() {
  echo "--> Verifying rebuilt venv Python interpreter..."
  local venv_py_target
  venv_py_target="$(run_as_service_user "readlink -f \"$APP_ROOT/.venv/bin/python3\" 2>/dev/null" || true)"
  if [[ -n "$venv_py_target" && ! -x "$venv_py_target" ]]; then
    echo "--> FIXING: $venv_py_target lacks execute bit; adding..."
    if command -v sudo >/dev/null 2>&1; then
      sudo chmod +x "$venv_py_target"
    else
      chmod +x "$venv_py_target"
    fi
  fi
  run_as_service_user "\"$APP_ROOT/.venv/bin/python3\" --version"
}

validate_runtime_bootstrap() {
  local key
  local command
  command="export PATH=\"$PATH_PREFIX:\$PATH\"; cd \"$APP_ROOT\" && export PYTHONPATH=src && ./.venv/bin/python scripts/validate_runtime_bootstrap.py --bootstrap-env-file \"$APP_ROOT/.env\""
  if [[ -n "$PROFILE" ]]; then
    command+=" --profile $PROFILE"
  fi
  command+=" --expect-environment $EXPECT_ENVIRONMENT"
  command+=" --expect-runtime-stage $EXPECT_RUNTIME_STAGE"
  command+=" --expect-factory-role $EXPECT_FACTORY_ROLE"
  command+=" --expect-deployment-profile $EXPECT_DEPLOYMENT_PROFILE"
  command+=" --expect-machine-slug $EXPECT_MACHINE_SLUG"
  for key in "${REQUIRED_KEYS[@]}"; do
    command+=" --require $key"
  done
  command+=" --json"

  echo "--> Validating runtime bootstrap..."
  run_as_service_user "$command"
}

verify_observability_runtime() {
  echo "--> Verifying real Logfire/OpenTelemetry imports before restart..."
  run_as_service_user "export PATH=\"$PATH_PREFIX:\$PATH\"; cd \"$APP_ROOT\" && ./.venv/bin/python scripts/verify_observability_runtime.py --json"
}

verify_service_imports() {
  echo "--> Verifying service entrypoint imports before restart..."
  run_as_service_user "export PATH=\"$PATH_PREFIX:\$PATH\"; cd \"$APP_ROOT\" && export PYTHONPATH=src && ./.venv/bin/python scripts/verify_service_imports.py"
}

run_validation_cycle() {
  validate_runtime_bootstrap &&
  verify_observability_runtime &&
  verify_service_imports
}

ensure_runtime_is_ready() {
  ensure_existing_venv_is_usable
  ensure_python_runtime
  sync_dependencies
  ensure_venv_python_executable

  if ! run_validation_cycle; then
    echo "--> Runtime preflight failed after sync; rebuilding .venv from scratch..."
    remove_runtime_venv
    sync_dependencies
    ensure_venv_python_executable
    echo "--> Re-running runtime bootstrap, observability, and service import validation after clean rebuild..."
    run_validation_cycle
  fi
}

ensure_runtime_is_ready
