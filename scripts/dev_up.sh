#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Universal Agent — Local Dev Stack (start)
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="/tmp/ua-local-dev.pids"
LOG_DIR="/tmp/ua-local-logs"
LOCAL_DATA_DIR="${UA_LOCAL_DATA_DIR:-$HOME/universal_agent_local_data}"

GATEWAY_PORT=8002
API_PORT=8001
WEBUI_PORT=3000

banner() {
  echo ""
  echo "=========================================="
  echo " universal_agent — local dev stack"
  echo "  start:  ./scripts/dev_up.sh   (this)"
  echo "  stop:   ./scripts/dev_down.sh"
  echo "  status: ./scripts/dev_status.sh"
  echo "  reset:  ./scripts/dev_reset.sh"
  echo "=========================================="
  echo ""
}

usage() {
  banner
  echo "Usage: ./scripts/dev_up.sh [--help]"
  echo ""
  echo "Starts the local development stack:"
  echo "  • Gateway  on http://localhost:${GATEWAY_PORT}"
  echo "  • API      on http://localhost:${API_PORT}"
  echo "  • Web UI   on http://localhost:${WEBUI_PORT}"
  echo ""
  echo "Prerequisites:"
  echo "  • INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, INFISICAL_PROJECT_ID in env"
  echo "  • infisical CLI installed"
  echo "  • uv, node, npm on PATH (node via nvm is auto-sourced)"
  echo ""
  echo "All secrets are injected via 'infisical run' — no plaintext on disk."
  echo "Local state writes to: ${LOCAL_DATA_DIR}/"
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || "${1:-}" == "help" ]]; then
  usage
  exit 0
fi

banner

# --------------------------------------------------------------------------
# 1. Source nvm if node is not on PATH
# --------------------------------------------------------------------------
if ! command -v node &>/dev/null; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    echo "Sourcing nvm from ${NVM_DIR}/nvm.sh ..."
    # shellcheck disable=SC1091
    source "$NVM_DIR/nvm.sh"
  fi
fi

# --------------------------------------------------------------------------
# 2. Preflight checks
# --------------------------------------------------------------------------
echo "Running preflight checks..."

_missing=()
[[ -z "${INFISICAL_CLIENT_ID:-}" ]]     && _missing+=("INFISICAL_CLIENT_ID")
[[ -z "${INFISICAL_CLIENT_SECRET:-}" ]] && _missing+=("INFISICAL_CLIENT_SECRET")
[[ -z "${INFISICAL_PROJECT_ID:-}" ]]    && _missing+=("INFISICAL_PROJECT_ID")

if [[ ${#_missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing Infisical bootstrap env vars: ${_missing[*]}"
  echo ""
  echo "Add these to your shell profile (~/.bashrc):"
  echo '  export INFISICAL_CLIENT_ID="st.xxxxx..."'
  echo '  export INFISICAL_CLIENT_SECRET="st.yyyyy..."'
  echo '  export INFISICAL_PROJECT_ID="proj-xxxxx..."'
  exit 1
fi

_missing_bins=()
command -v infisical &>/dev/null || _missing_bins+=("infisical  → https://infisical.com/docs/cli/overview")
command -v uv        &>/dev/null || _missing_bins+=("uv         → pip install uv")
command -v node      &>/dev/null || _missing_bins+=("node       → nvm install --lts")
command -v npm       &>/dev/null || _missing_bins+=("npm        → comes with node")

if [[ ${#_missing_bins[@]} -gt 0 ]]; then
  echo "ERROR: Missing required binaries:"
  for b in "${_missing_bins[@]}"; do echo "  • $b"; done
  exit 1
fi

# Check if ports are in use
_busy_ports=()
for p in $GATEWAY_PORT $API_PORT $WEBUI_PORT; do
  if lsof -i ":$p" &>/dev/null 2>&1; then
    _busy_ports+=("$p")
  fi
done
if [[ ${#_busy_ports[@]} -gt 0 ]]; then
  echo "ERROR: Ports already in use: ${_busy_ports[*]}"
  echo "Run ./scripts/dev_down.sh first, or check what is using them:"
  for p in "${_busy_ports[@]}"; do echo "  lsof -i :$p"; done
  exit 1
fi

echo "  ✓ Infisical bootstrap creds set"
echo "  ✓ Required binaries found (infisical, uv, node, npm)"
echo "  ✓ Ports ${API_PORT}, ${GATEWAY_PORT}, ${WEBUI_PORT} available"

# --------------------------------------------------------------------------
# 3. Authenticate to Infisical
# --------------------------------------------------------------------------
echo ""
echo "Authenticating to Infisical..."
INFISICAL_TOKEN="$(infisical login \
    --method=universal-auth \
    --client-id="${INFISICAL_CLIENT_ID}" \
    --client-secret="${INFISICAL_CLIENT_SECRET}" \
    --silent --plain)"
export INFISICAL_TOKEN
echo "  ✓ Infisical authenticated"

# --------------------------------------------------------------------------
# 4. Create directories
# --------------------------------------------------------------------------
mkdir -p "$LOCAL_DATA_DIR"
mkdir -p "$LOG_DIR"
: > "$PID_FILE"
echo "  ✓ Data dir: ${LOCAL_DATA_DIR}"
echo "  ✓ Log dir:  ${LOG_DIR}"

# --------------------------------------------------------------------------
# 5. Ensure Python venv is ready
# --------------------------------------------------------------------------
if [[ ! -d "${REPO_ROOT}/.venv" ]]; then
  echo ""
  echo "Setting up Python venv (first run)..."
  (cd "$REPO_ROOT" && uv sync --frozen --no-dev)
fi

# --------------------------------------------------------------------------
# 6. Render web-ui/.env.local from Infisical local environment
# --------------------------------------------------------------------------
echo ""
echo "Rendering web-ui/.env.local from Infisical 'local' environment..."

# The render script needs the Infisical SDK to pull values. We run it inside
# 'infisical run' so all secrets from the local environment are in the env,
# then the script picks out the ones the web-ui needs.
infisical run --env=local --projectId="${INFISICAL_PROJECT_ID}" -- \
    "${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/scripts/render_service_env_from_infisical.py" \
    --output "${REPO_ROOT}/web-ui/.env.local" \
    --allow-missing \
    --include-runtime-identity \
    --entry "UA_DASHBOARD_OPS_TOKEN=UA_DASHBOARD_OPS_TOKEN,UA_OPS_TOKEN" \
    --entry "UA_DASHBOARD_SESSION_SECRET=UA_DASHBOARD_SESSION_SECRET,UA_OPS_TOKEN" \
    --entry "UA_DASHBOARD_PASSWORD=UA_DASHBOARD_PASSWORD" \
    --entry "UA_DASHBOARD_OWNER_ID=UA_DASHBOARD_OWNER_ID" \
    --entry "UA_DASHBOARD_OWNERS_JSON=UA_DASHBOARD_OWNERS_JSON"

echo "  ✓ web-ui/.env.local written"

# --------------------------------------------------------------------------
# 7. Install web-ui deps if needed
# --------------------------------------------------------------------------
if [[ ! -d "${REPO_ROOT}/web-ui/node_modules" ]]; then
  echo ""
  echo "Installing web-ui dependencies (first run)..."
  (cd "${REPO_ROOT}/web-ui" && npm install)
fi

# --------------------------------------------------------------------------
# 8. Start services
# --------------------------------------------------------------------------
echo ""
echo "Starting services..."

start_service() {
  local name="$1"; shift
  echo "  Starting ${name}..."
  "$@" > "${LOG_DIR}/${name}.log" 2>&1 &
  local pid=$!
  echo "${pid}:${name}" >> "$PID_FILE"
  echo "    PID ${pid} → ${LOG_DIR}/${name}.log"
}

# Gateway
start_service "gateway" \
  infisical run --env=local --projectId="${INFISICAL_PROJECT_ID}" -- \
  "${REPO_ROOT}/.venv/bin/python" -m universal_agent.gateway_server

# API
start_service "api" \
  infisical run --env=local --projectId="${INFISICAL_PROJECT_ID}" -- \
  "${REPO_ROOT}/.venv/bin/python" -m universal_agent.api.server

# Web UI (Next.js dev server)
start_service "webui" \
  bash -c "cd '${REPO_ROOT}/web-ui' && exec ./node_modules/.bin/next dev --port ${WEBUI_PORT}"

# --------------------------------------------------------------------------
# 9. Wait and verify
# --------------------------------------------------------------------------
echo ""
echo "Waiting for services to start..."
sleep 4

_ok=true
for svc_port in "API:${API_PORT}" "Gateway:${GATEWAY_PORT}" "WebUI:${WEBUI_PORT}"; do
  name="${svc_port%%:*}"
  port="${svc_port##*:}"
  if curl -sf "http://localhost:${port}" >/dev/null 2>&1 || \
     curl -sf "http://localhost:${port}/health" >/dev/null 2>&1; then
    echo "  ✓ ${name} responding on :${port}"
  else
    echo "  ⚠ ${name} not yet responding on :${port} (may still be starting)"
    echo "    Check: tail -f ${LOG_DIR}/${name,,}.log"
    _ok=false
  fi
done

echo ""
echo "=========================================="
echo " Stack running:"
echo "   Web UI:   http://localhost:${WEBUI_PORT}"
echo "   Gateway:  http://localhost:${GATEWAY_PORT}"
echo "   API:      http://localhost:${API_PORT}"
echo "   Logs:     ${LOG_DIR}/"
echo "   Data:     ${LOCAL_DATA_DIR}/"
echo "   PIDs:     ${PID_FILE}"
echo "   Stop:     ./scripts/dev_down.sh"
echo "=========================================="
echo ""

if [[ "$_ok" == "false" ]]; then
  echo "Some services may still be starting. Check logs with:"
  echo "  tail -f ${LOG_DIR}/*.log"
fi
