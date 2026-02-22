# 66. Tailnet-First DevOps Phase A Implementation (2026-02-22)

## Summary
This document records the implementation of Phase A from the Tailnet-first DevOps plan (`63`).

Date: 2026-02-22  
Status: Implemented (Phase A scope)

## Scope Implemented
Phase A targets from `63`:
1. Canonical remote host defaults moved to MagicDNS.
2. Tailnet preflight checks added before deploy/sync/control operations.
3. Fail-fast behavior with break-glass bypass controls.

## Files Changed
1. `scripts/deploy_vps.sh`
2. `scripts/vpsctl.sh`
3. `scripts/sync_remote_workspaces.sh`
4. `scripts/pull_remote_workspaces_now.sh`

## Detailed Changes

### 1. MagicDNS host canonicalization
Updated default remote host values from `root@100.106.113.93` to:
- `root@srv1360701.taildcc090.ts.net`

Applied in:
1. `scripts/deploy_vps.sh`
2. `scripts/vpsctl.sh`
3. `scripts/sync_remote_workspaces.sh`
4. `scripts/pull_remote_workspaces_now.sh`

### 2. Tailnet preflight checks
Added tailnet preflight logic for operations that perform remote activity:
1. `tailscale status` must pass.
2. `tailscale ping <host>` must pass.

Behavior policy:
1. `UA_TAILNET_PREFLIGHT=auto` (default): run preflight for tailnet-like hosts (MagicDNS / `100.*`).
2. `UA_TAILNET_PREFLIGHT=off`: disable preflight.
3. `UA_TAILNET_PREFLIGHT=force`: require preflight regardless of host pattern.
4. `UA_SKIP_TAILNET_PREFLIGHT=true`: break-glass bypass.

### 3. Sync-specific behavior
In `scripts/sync_remote_workspaces.sh`:
1. Preflight runs before each sync cycle (`sync_once`).
2. If preflight fails, cycle is skipped with warning (non-fatal in continuous mode).
3. `--status-json` returns structured error when preflight fails.

## Validation Performed
1. Shell syntax validation:
- `bash -n scripts/deploy_vps.sh scripts/vpsctl.sh scripts/sync_remote_workspaces.sh scripts/pull_remote_workspaces_now.sh`
2. Basic command smoke checks:
- `scripts/vpsctl.sh --help`
- `scripts/pull_remote_workspaces_now.sh --help`
3. Sync status probe:
- `UA_SKIP_TAILNET_PREFLIGHT=true scripts/sync_remote_workspaces.sh --status-json`

Observed in current local environment:
- DNS resolution for `srv1360701.taildcc090.ts.net` was unavailable, producing expected remote-host resolution failure in status probe output.
- This is environment/network state, not script syntax failure.

## Notes and Implications
1. Phase A is now code-complete for script-side host/preflight hardening.
2. Operational success requires the local machine to have active Tailscale routing/DNS for the configured MagicDNS host.
3. Break-glass controls are available if immediate operations are needed while tailnet preconditions are being fixed.

## Next Steps (Phase B)
Continue `63` with tailnet-only staging:
1. Implement `tailscale serve` routing script and idempotent setup.
2. Add deploy post-checks for private staging UI/API health.
3. Keep VPS runtime as source of truth for VP behavior verification.
