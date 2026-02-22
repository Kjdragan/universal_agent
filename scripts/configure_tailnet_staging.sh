#!/usr/bin/env bash
set -euo pipefail

MODE="ensure"
UI_HTTPS_PORT="${UA_TAILNET_STAGING_UI_HTTPS_PORT:-443}"
UI_TARGET="${UA_TAILNET_STAGING_UI_TARGET:-http://127.0.0.1:3000}"
API_HTTPS_PORT="${UA_TAILNET_STAGING_API_HTTPS_PORT:-8443}"
API_TARGET="${UA_TAILNET_STAGING_API_TARGET:-http://127.0.0.1:8002}"
API_HEALTH_PATH="${UA_TAILNET_STAGING_API_HEALTH_PATH:-/api/v1/health}"
UI_HEALTH_PATH="${UA_TAILNET_STAGING_UI_HEALTH_PATH:-/}"
HEALTH_MAX_ATTEMPTS="${UA_TAILNET_STAGING_HEALTH_MAX_ATTEMPTS:-12}"
HEALTH_SLEEP_SECONDS="${UA_TAILNET_STAGING_HEALTH_SLEEP_SECONDS:-5}"

usage() {
  cat <<'EOF'
Usage: scripts/configure_tailnet_staging.sh [--ensure|--verify-only|--reset] [--quiet]

Purpose:
  Configure tailnet-only staging proxies using tailscale serve and verify local health.

Environment overrides:
  UA_TAILNET_STAGING_UI_HTTPS_PORT   (default: 443)
  UA_TAILNET_STAGING_UI_TARGET       (default: http://127.0.0.1:3000)
  UA_TAILNET_STAGING_API_HTTPS_PORT  (default: 8443)
  UA_TAILNET_STAGING_API_TARGET      (default: http://127.0.0.1:8002)
  UA_TAILNET_STAGING_UI_HEALTH_PATH  (default: /)
  UA_TAILNET_STAGING_API_HEALTH_PATH (default: /api/v1/health)
EOF
}

log() {
  if [[ "${QUIET:-false}" != "true" ]]; then
    printf '[tailnet-staging] %s\n' "$*"
  fi
}

require_command() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Missing required command: ${name}" >&2
    exit 1
  fi
}

is_positive_int() {
  [[ "${1:-}" =~ ^[0-9]+$ ]] && [[ "${1:-0}" != "0" ]]
}

check_tailscale_ready() {
  require_command tailscale
  if ! tailscale status >/dev/null 2>&1; then
    echo "tailscale status check failed; tailscaled may be unavailable." >&2
    return 1
  fi
}

configure_serve() {
  log "Configuring tailnet staging serve routes..."
  local out=""
  if ! out="$(tailscale serve --yes --bg --https="${UI_HTTPS_PORT}" "${UI_TARGET}" 2>&1)"; then
    if grep -qi "Serve is not enabled on your tailnet" <<< "${out}"; then
      echo "Tailnet policy blocker: tailscale serve is not enabled for this tailnet/node." >&2
      echo "${out}" >&2
      return 1
    fi
    echo "${out}" >&2
    return 1
  fi
  if ! out="$(tailscale serve --yes --bg --https="${API_HTTPS_PORT}" "${API_TARGET}" 2>&1)"; then
    if grep -qi "Serve is not enabled on your tailnet" <<< "${out}"; then
      echo "Tailnet policy blocker: tailscale serve is not enabled for this tailnet/node." >&2
      echo "${out}" >&2
      return 1
    fi
    echo "${out}" >&2
    return 1
  fi
}

verify_local_health() {
  require_command curl
  local ui_code api_code attempt
  local ui_url="${UI_TARGET%/}${UI_HEALTH_PATH}"
  local api_url="${API_TARGET%/}${API_HEALTH_PATH}"

  attempt=1
  while true; do
    ui_code="$(curl -s -o /dev/null -w '%{http_code}' "${ui_url}" || true)"
    api_code="$(curl -s -o /dev/null -w '%{http_code}' "${api_url}" || true)"
    if [[ "${ui_code}" == "200" && "${api_code}" == "200" ]]; then
      log "Local health OK (ui=${ui_code}, api=${api_code})."
      return 0
    fi
    if [[ "${attempt}" -ge "${HEALTH_MAX_ATTEMPTS}" ]]; then
      echo "Local health check failed after ${HEALTH_MAX_ATTEMPTS} attempts." >&2
      echo "UI:  ${ui_url} (HTTP ${ui_code:-n/a})" >&2
      echo "API: ${api_url} (HTTP ${api_code:-n/a})" >&2
      return 1
    fi
    log "Waiting for local health (attempt ${attempt}/${HEALTH_MAX_ATTEMPTS}, ui=${ui_code:-n/a}, api=${api_code:-n/a})..."
    attempt=$((attempt + 1))
    sleep "${HEALTH_SLEEP_SECONDS}"
  done
}

verify_serve_status() {
  local status
  status="$(tailscale serve status 2>&1 || true)"
  if ! grep -q "proxy ${UI_TARGET}" <<< "${status}"; then
    echo "tailscale serve status missing UI proxy target ${UI_TARGET}." >&2
    return 1
  fi
  if ! grep -q "proxy ${API_TARGET}" <<< "${status}"; then
    echo "tailscale serve status missing API proxy target ${API_TARGET}." >&2
    return 1
  fi
  log "tailscale serve status includes UI/API proxy targets and expected ports."
}

reset_serve() {
  log "Resetting tailscale serve config..."
  tailscale serve reset --yes >/dev/null
}

QUIET="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ensure) MODE="ensure"; shift ;;
    --verify-only) MODE="verify-only"; shift ;;
    --reset) MODE="reset"; shift ;;
    --quiet) QUIET="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! is_positive_int "${UI_HTTPS_PORT}"; then
  UI_HTTPS_PORT="443"
fi
if ! is_positive_int "${UI_HTTPS_PORT}"; then
  echo "Invalid UA_TAILNET_STAGING_UI_HTTPS_PORT: ${UI_HTTPS_PORT}" >&2
  exit 1
fi
if ! is_positive_int "${API_HTTPS_PORT}"; then
  API_HTTPS_PORT="8443"
fi
if ! is_positive_int "${API_HTTPS_PORT}"; then
  echo "Invalid UA_TAILNET_STAGING_API_HTTPS_PORT: ${API_HTTPS_PORT}" >&2
  exit 1
fi
if ! is_positive_int "${HEALTH_MAX_ATTEMPTS}"; then
  HEALTH_MAX_ATTEMPTS="12"
fi
if ! is_positive_int "${HEALTH_SLEEP_SECONDS}"; then
  HEALTH_SLEEP_SECONDS="5"
fi

check_tailscale_ready

case "${MODE}" in
  reset)
    reset_serve
    log "Reset complete."
    ;;
  verify-only)
    verify_local_health
    verify_serve_status
    log "Verification complete."
    ;;
  ensure)
    configure_serve
    verify_local_health
    verify_serve_status
    log "Tailnet staging configured and verified."
    ;;
  *)
    echo "Unsupported mode: ${MODE}" >&2
    exit 1
    ;;
esac
