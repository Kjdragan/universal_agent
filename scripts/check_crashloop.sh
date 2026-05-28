#!/usr/bin/env bash
# Crashloop fail-fast helper for deploy.yml's check_local_health loop.
#
# Tracks systemd unit restarts across calls via a /tmp cache. On first
# call for a given unit it stores the baseline NRestarts. On subsequent
# calls (past attempt 3) it computes the delta and exits 1 (with
# diagnostics) if the unit has restarted >= threshold times.
#
# Args:
#   $1 = service name (display, e.g. "gateway")
#   $2 = systemd unit (e.g. "universal-agent-gateway.service")
#   $3 = current attempt number
#   $4 = threshold (default 5)
#
# Exit:
#   0 = keep waiting / no crashloop / not applicable
#   1 = crashloop detected, abort the wait loop
#
# Lifted out of deploy.yml inline because GHA's workflow validator
# rejected the equivalent inline shell. See memory:
# 2026-05-27-deployyml-parser-quirk.

set -u

name="${1:-}"
service_unit="${2:-}"
attempt="${3:-0}"
threshold="${4:-5}"

if [ -z "$service_unit" ]; then
    exit 0
fi
if ! command -v systemctl >/dev/null 2>&1; then
    exit 0
fi

cache="/tmp/ua-crashloop-${service_unit}.start"

# First call (cache absent): record baseline and pass.
if [ ! -f "$cache" ]; then
    systemctl show -p NRestarts --value "$service_unit" 2>/dev/null > "$cache" || echo 0 > "$cache"
    exit 0
fi

# Let the first 2 attempts pass so a slow first cycle isn't penalized.
if [ "$attempt" -lt 3 ]; then
    exit 0
fi

start_restarts="$(cat "$cache" 2>/dev/null || echo 0)"
now_restarts="$(systemctl show -p NRestarts --value "$service_unit" 2>/dev/null || echo 0)"

# Guard against non-numeric output.
if ! [ "$now_restarts" -ge 0 ] 2>/dev/null; then
    exit 0
fi
if ! [ "$start_restarts" -ge 0 ] 2>/dev/null; then
    exit 0
fi

delta=$((now_restarts - start_restarts))
if [ "$delta" -lt "$threshold" ]; then
    exit 0
fi

echo "::error::${name} crashloop detected: ${service_unit} restarted ${delta} times during health-wait (threshold=${threshold}). Failing fast instead of waiting for the full retry budget."
echo "--> Last journal entries from ${service_unit}:"
journalctl -u "$service_unit" --no-pager -n 80 2>/dev/null | tail -80 || true
exit 1
