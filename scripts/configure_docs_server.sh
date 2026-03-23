#!/usr/bin/env bash
# configure_docs_server.sh — Set up MkDocs docs serving via tailscale serve
#
# Usage:
#   ./scripts/configure_docs_server.sh [--ensure|--verify-only|--reset]
#
# Modes:
#   --ensure       Build docs, install/start systemd unit, configure tailscale serve (default)
#   --verify-only  Check that the docs server is running and accessible
#   --reset        Stop the docs server, remove tailscale serve entry
#
# This script is designed to run on the VPS as root.

set -euo pipefail

DOCS_PORT="${UA_DOCS_PORT:-8100}"
DOCS_TAILSCALE_PORT="${UA_DOCS_TAILSCALE_PORT:-8100}"
CHECKOUT_DIR="${UA_DOCS_CHECKOUT:-/opt/universal_agent}"
SERVICE_NAME="universal-agent-docs"
MODE="${1:---ensure}"

log() { echo "[docs-server] $*"; }
err() { echo "[docs-server] ERROR: $*" >&2; }

# ── Helpers ──

build_docs() {
    log "Building MkDocs site..."
    local ua_home
    ua_home=$(getent passwd ua | cut -d: -f6)
    sudo -H -u ua bash -c "cd ${CHECKOUT_DIR} && .venv/bin/mkdocs build -f mkdocs.yml --quiet"
    log "MkDocs site built at ${CHECKOUT_DIR}/site/"
}

install_service() {
    local unit_src="${CHECKOUT_DIR}/infrastructure/systemd/${SERVICE_NAME}.service"
    local unit_dst="/etc/systemd/system/${SERVICE_NAME}.service"

    if [ ! -f "$unit_src" ]; then
        err "Systemd unit not found at ${unit_src}"
        return 1
    fi

    log "Installing systemd unit..."
    cp "$unit_src" "$unit_dst"
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}"
    log "Service ${SERVICE_NAME} started on port ${DOCS_PORT}"
}

configure_tailscale_serve() {
    log "Configuring tailscale serve for docs (HTTPS :${DOCS_TAILSCALE_PORT} -> localhost:${DOCS_PORT})..."

    # Check if tailscale serve is available
    if ! command -v tailscale &>/dev/null; then
        err "tailscale CLI not found"
        return 1
    fi

    tailscale serve --bg --https "${DOCS_TAILSCALE_PORT}" "http://127.0.0.1:${DOCS_PORT}" 2>&1 || {
        err "Failed to configure tailscale serve. Check tailnet policy."
        return 1
    }

    log "tailscale serve configured: https://<tailnet-host>:${DOCS_TAILSCALE_PORT}"
}

verify() {
    local ok=true

    # Check systemd service
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        log "✅ ${SERVICE_NAME} systemd unit is active"
    else
        err "❌ ${SERVICE_NAME} systemd unit is not active"
        ok=false
    fi

    # Check localhost health
    if curl -sf "http://127.0.0.1:${DOCS_PORT}/" >/dev/null 2>&1; then
        log "✅ Docs server responding on localhost:${DOCS_PORT}"
    else
        err "❌ Docs server not responding on localhost:${DOCS_PORT}"
        ok=false
    fi

    # Check tailscale serve status
    if tailscale serve status 2>/dev/null | grep -q "${DOCS_PORT}"; then
        log "✅ tailscale serve entry found for port ${DOCS_PORT}"
    else
        err "❌ No tailscale serve entry for port ${DOCS_PORT}"
        ok=false
    fi

    $ok
}

reset() {
    log "Resetting docs server..."

    # Stop systemd service
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true

    # Remove tailscale serve entry
    tailscale serve --https "${DOCS_TAILSCALE_PORT}" off 2>/dev/null || true

    log "Docs server reset complete"
}

# ── Main ──

case "$MODE" in
    --ensure)
        build_docs
        install_service
        configure_tailscale_serve
        verify
        log "Done. Docs available at https://<your-tailnet-host>:${DOCS_TAILSCALE_PORT}/"
        ;;
    --verify-only)
        verify
        ;;
    --reset)
        reset
        ;;
    *)
        err "Unknown mode: ${MODE}"
        echo "Usage: $0 [--ensure|--verify-only|--reset]"
        exit 1
        ;;
esac
