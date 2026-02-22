#!/usr/bin/env bash
set -euo pipefail

MODE="ensure"
UI_HTTPS_PORT="${UA_TAILNET_STAGING_UI_HTTPS_PORT:-443}"
UI_TARGET="${UA_TAILNET_STAGING_UI_TARGET:-http://127.0.0.1:3000}"
API_HTTPS_PORT="${UA_TAILNET_STAGING_API_HTTPS_PORT:-8443}"
API_TARGET="${UA_TAILNET_STAGING_API_TARGET:-http://127.0.0.1:8001}"
API_HEALTH_PATH="${UA_TAILNET_STAGING_API_HEALTH_PATH:-/api/v1/health}"
UI_HEALTH_PATH="${UA_TAILNET_STAGING_UI_HEALTH_PATH:-/}"

usage() {
  cat <<'EOF'
Usage: scripts/configure_tailnet_staging.sh [--ensure|--verify-only|--reset] [--quiet]

Purpose:
  Configure tailnet-only staging proxies using tailscale serve and verify local health.

Environment overrides:
  UA_TAILNET_STAGING_UI_HTTPS_PORT   (default: 443)
  UA_TAILNET_STAGING_UI_TARGET       (default: http://127.0.0.1:3000)
  UA_TAILNET_STAGING_API_HTTPS_PORT  (default: 8443)
  UA_TAILNET_STAGING_API_TARGET      (default: http://127.0.0.1:8001)
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
  tailscale serve --yes --bg --https="${UI_HTTPS_PORT}" "${UI_TARGET}" >/dev/null
  tailscale serve --yes --bg --https="${API_HTTPS_PORT}" "${API_TARGET}" >/dev/null
}

verify_local_health() {
  require_command curl
  local ui_code api_code

  ui_code="$(curl -s -o /dev/null -w '%{http_code}' "${UI_TARGET%/}${UI_HEALTH_PATH}" || true)"
  api_code="$(curl -s -o /dev/null -w '%{http_code}' "${API_TARGET%/}${API_HEALTH_PATH}" || true)"

  if [[ "${ui_code}" != "200" ]]; then
    echo "UI local health check failed: ${UI_TARGET%/}${UI_HEALTH_PATH} (HTTP ${ui_code:-n/a})" >&2
    return 1
  fi
  if [[ "${api_code}" != "200" ]]; then
    echo "API local health check failed: ${API_TARGET%/}${API_HEALTH_PATH} (HTTP ${api_code:-n/a})" >&2
    return 1
  fi

  log "Local health OK (ui=${ui_code}, api=${api_code})."
}

verify_serve_status() {
  local status
  status="$(tailscale serve status 2>&1 || true)"
  if ! grep -q ":${UI_HTTPS_PORT}" <<< "${status}"; then
    echo "tailscale serve status missing UI HTTPS port ${UI_HTTPS_PORT}." >&2
    return 1
  fi
  if ! grep -q ":${API_HTTPS_PORT}" <<< "${status}"; then
    echo "tailscale serve status missing API HTTPS port ${API_HTTPS_PORT}." >&2
    return 1
  fi
  log "tailscale serve status includes ui_port=${UI_HTTPS_PORT} api_port=${API_HTTPS_PORT}."
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
  echo "Invalid UA_TAILNET_STAGING_UI_HTTPS_PORT: ${UI_HTTPS_PORT}" >&2
  exit 1
fi
if ! is_positive_int "${API_HTTPS_PORT}"; then
  echo "Invalid UA_TAILNET_STAGING_API_HTTPS_PORT: ${API_HTTPS_PORT}" >&2
  exit 1
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

