#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
APP_USER="${APP_USER:-ua}"
INFISICAL_PYTHON_VERSION="${INFISICAL_PYTHON_VERSION:-2.3.5}"
PYO3_FORWARD_COMPAT="${PYO3_USE_ABI3_FORWARD_COMPATIBILITY:-1}"

usage() {
  cat <<'EOF'
Install/verify Infisical Python SDK build prerequisites for Python 3.13 VPS runtimes.

Modes:
  --prepare-only   Install rustup stable toolchain for APP_USER only.
  --install-only   Install infisical-python into APP_ROOT/.venv only.
  --verify-only    Verify infisical_client import only.
  (default)        Prepare + install + verify.

Environment:
  APP_ROOT                               App root (default: /opt/universal_agent)
  APP_USER                               Service user (default: ua)
  INFISICAL_PYTHON_VERSION               SDK version (default: 2.3.5)
  PYO3_USE_ABI3_FORWARD_COMPATIBILITY    PyO3 forward-compat flag (default: 1)
EOF
}

MODE="${1:-full}"
case "${MODE}" in
  --prepare-only|--install-only|--verify-only|full)
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "ERROR: unknown mode '${MODE}'" >&2
    usage >&2
    exit 2
    ;;
esac

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  echo "ERROR: APP_USER does not exist: ${APP_USER}" >&2
  exit 3
fi

if [ ! -d "${APP_ROOT}" ]; then
  echo "ERROR: APP_ROOT does not exist: ${APP_ROOT}" >&2
  exit 4
fi

if [ ! -x "${APP_ROOT}/.venv/bin/python3" ]; then
  echo "ERROR: expected venv interpreter missing: ${APP_ROOT}/.venv/bin/python3" >&2
  exit 5
fi

run_as_app_user() {
  local command="$1"
  runuser -u "${APP_USER}" -- bash -lc "${command}"
}

prepare_toolchain() {
  run_as_app_user '
    set -euo pipefail
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
    if ! command -v rustup >/dev/null 2>&1; then
      curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal --default-toolchain stable
    fi
    source "$HOME/.cargo/env"
    rustup default stable >/dev/null
    rustc --version
    cargo --version
  '
}

install_sdk() {
  run_as_app_user "
    set -euo pipefail
    export PATH=\"\$HOME/.local/bin:\$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:\$PATH\"
    source \"\$HOME/.cargo/env\"
    cd '${APP_ROOT}'
    export PYO3_USE_ABI3_FORWARD_COMPATIBILITY='${PYO3_FORWARD_COMPAT}'
    uv pip install --python .venv/bin/python3 infisical-python==${INFISICAL_PYTHON_VERSION}
  "
}

verify_sdk() {
  run_as_app_user "
    set -euo pipefail
    export PATH=\"\$HOME/.local/bin:\$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:\$PATH\"
    cd '${APP_ROOT}'
    PYTHONPATH='${APP_ROOT}/src' .venv/bin/python3 - <<'PY'
import infisical_client
print('infisical_client import OK')
PY
  "
}

case "${MODE}" in
  --prepare-only)
    prepare_toolchain
    ;;
  --install-only)
    install_sdk
    ;;
  --verify-only)
    verify_sdk
    ;;
  full)
    prepare_toolchain
    install_sdk
    verify_sdk
    ;;
esac

echo "Infisical SDK setup mode '${MODE}' completed."
