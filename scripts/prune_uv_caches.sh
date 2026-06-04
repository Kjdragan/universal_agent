#!/usr/bin/env bash
# Prune all four UA uv caches to bound disk growth. Idempotent, fast, and
# non-fatal per cache. SINGLE SOURCE OF TRUTH — called from two places:
#   1. scripts/deploy/remote_deploy.sh — once per deploy (~19x/day).
#   2. universal-agent-uv-cache-prune.service (a daily systemd timer) — a
#      deploy-INDEPENDENT backstop so reclamation keeps happening even if
#      deploys stall for days (this box has a documented deploy-outage history).
#
# Root cause it addresses (2026-06-04): `uv sync` (deploy_validate_runtime.sh)
# + `uv tool install --force notebooklm-mcp-cli` (floating/unpinned) run every
# deploy, and runtime workers add two more cache trees (worker.py ->
# $APP_ROOT/.uv-cache, hooks.py -> /tmp/uv_cache). Nothing ever pruned, so the
# four caches (ua ~/.cache/uv, root /root/.cache/uv, /tmp/uv_cache,
# $APP_ROOT/.uv-cache) reached ~65G and drove disk 70%->78% over ~2 weeks.
#
# `uv cache prune --ci` removes re-downloadable wheels/sdists but RETAINS the
# unpacked/built archives that live environments hardlink from, so running
# services are unaffected (verified 2026-06-04: gateway/api/webui/vp-workers/
# csi all healthy through a 64.7G reclaim that took disk 78%->58%). `--force`
# is REQUIRED: the caches are locked nearly continuously by long-running
# `uv run` services (vp workers, csi uvicorn, discord) + cron/hook churn, so a
# lock-respecting prune times out even at UV_LOCK_TIMEOUT=300s (measured).
# `--force` skips the in-use lock wait; UV_LOCK_TIMEOUT is a defensive cap on
# the brief lock-acquisition step `--force` still performs — do NOT drop
# `--force` to "resolve" the apparent contradiction. Full rationale:
# project_docs/06_platform/04_deployment_and_cicd.md.
#
# Runs as root (systemd timer) OR as a passwordless sudoer (the deploy SSH
# user): it `sudo`s to `ua` for the three ua-owned caches and runs root's uv
# for /root/.cache/uv. NOT `set -e` on purpose — one cache's prune failure must
# not abort the others.
set -uo pipefail

APP_ROOT="${APP_ROOT:-/opt/universal_agent}"
UA_USER="${UA_PRUNE_USER:-ua}"
UA_HOME="${UA_PRUNE_HOME:-/home/ua}"
UA_UV="${UA_PRUNE_UV:-$UA_HOME/.local/bin/uv}"
ROOT_UV_DEFAULT="${ROOT_PRUNE_UV:-/root/.local/bin/uv}"
LOCK_TIMEOUT="${UV_PRUNE_LOCK_TIMEOUT:-10}"

if ! command -v sudo >/dev/null 2>&1; then
  echo "prune_uv_caches: sudo unavailable; cannot prune as ua/root — skipping." >&2
  exit 0
fi

# Prune one ua-owned cache. $1 = optional UV_CACHE_DIR (empty => ua default
# ~/.cache/uv). Uses the absolute uv path so it does not depend on login PATH.
prune_ua_cache() {
  local cache_dir="${1:-}"
  if [ -n "$cache_dir" ]; then
    sudo -H -u "$UA_USER" env HOME="$UA_HOME" UV_CACHE_DIR="$cache_dir" \
      UV_LOCK_TIMEOUT="$LOCK_TIMEOUT" "$UA_UV" cache prune --ci --force \
      || echo "WARN: uv cache prune failed for $cache_dir (non-fatal)"
  else
    sudo -H -u "$UA_USER" env HOME="$UA_HOME" \
      UV_LOCK_TIMEOUT="$LOCK_TIMEOUT" "$UA_UV" cache prune --ci --force \
      || echo "WARN: uv cache prune failed for $UA_USER default cache (non-fatal)"
  fi
}

echo "--> Pruning uv caches (reclaim re-downloadable wheels/sdists; keep built archives)..."
prune_ua_cache ""                      # $UA_HOME/.cache/uv (default; vp workers + crons share it)
prune_ua_cache "/tmp/uv_cache"         # hooks.py UV_CACHE_DIR injection for `uv run`
prune_ua_cache "$APP_ROOT/.uv-cache"   # worker.py UV_CACHE_DIR setdefault (repo-root .uv-cache)

# root-owned cache, populated by root-context `uv run` (csi_ingester uvicorn +
# the ~14 CSI services that ExecStart `uv run` as root with no UV_CACHE_DIR).
if sudo test -d /root/.cache/uv; then
  root_uv="$ROOT_UV_DEFAULT"
  if ! sudo test -x "$root_uv"; then
    # NON-login probe (bash -c, not -lc) so a profile/MOTD banner cannot poison the path.
    root_uv="$(sudo -H bash -c 'command -v uv' 2>/dev/null || true)"
  fi
  if [ -n "$root_uv" ] && sudo test -x "$root_uv"; then
    sudo -H env HOME=/root UV_LOCK_TIMEOUT="$LOCK_TIMEOUT" "$root_uv" cache prune --ci --force \
      || echo "WARN: uv cache prune failed for /root/.cache/uv (non-fatal)"
  else
    echo "WARN: no uv binary found for root; skipping /root/.cache/uv prune"
  fi
fi

echo "--> uv cache prune complete."
