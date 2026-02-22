# 67. Tailnet-First DevOps Phase B Implementation (2026-02-22)

## Summary
This document records Phase B implementation from the Tailnet-first DevOps plan (`63`), focused on private staging setup via Tailscale Serve and deploy-time post-check integration.

Date: 2026-02-22  
Status: Implemented (Phase B scope)

## Scope Implemented
Phase B targets from `63`:
1. Add private staging routing via `tailscale serve` on VPS.
2. Add setup/verification script for idempotent staging configuration.
3. Add deploy post-check path for staging health.

## Files Changed
1. `scripts/configure_tailnet_staging.sh` (new)
2. `scripts/deploy_vps.sh` (updated integration)

## New Staging Script
File: `scripts/configure_tailnet_staging.sh`

Capabilities:
1. `--ensure` (default):
- configures `tailscale serve` routes for UI and API
- verifies local UI/API health checks
- validates serve status includes expected HTTPS ports
2. `--verify-only`:
- health-check and serve-status verification without mutating serve config
3. `--reset`:
- clears serve configuration via `tailscale serve reset --yes`

Default route model:
1. UI staging over Tailnet HTTPS port `443` -> `http://127.0.0.1:3000`
2. API staging over Tailnet HTTPS port `8443` -> `http://127.0.0.1:8002`

Environment controls:
1. `UA_TAILNET_STAGING_UI_HTTPS_PORT` (default `443`)
2. `UA_TAILNET_STAGING_UI_TARGET` (default `http://127.0.0.1:3000`)
3. `UA_TAILNET_STAGING_API_HTTPS_PORT` (default `8443`)
4. `UA_TAILNET_STAGING_API_TARGET` (default `http://127.0.0.1:8002`)
5. `UA_TAILNET_STAGING_UI_HEALTH_PATH` (default `/`)
6. `UA_TAILNET_STAGING_API_HEALTH_PATH` (default `/api/v1/health`)

## Deploy Integration
File: `scripts/deploy_vps.sh`

Added deploy-time staging setup block:
1. Ensures script is executable on VPS.
2. Runs `bash scripts/configure_tailnet_staging.sh --ensure` after service restart.
3. Supports strictness mode via:
- `UA_TAILNET_STAGING_MODE=auto|required|disabled`

Behavior:
1. `disabled`: skip staging setup.
2. `auto` (default): attempt setup; warn and continue on failure.
3. `required` (also `force`/`strict` aliases): fail deploy if staging setup fails.

## Validation Performed
1. Shell syntax checks:
- `bash -n scripts/configure_tailnet_staging.sh scripts/deploy_vps.sh`
2. CLI help check:
- `scripts/configure_tailnet_staging.sh --help`

## Operational Notes
1. Tailnet staging is private-by-design and intended for integration/debug checks before public smoke.
2. Public domains remain final acceptance path.
3. Phase B implementation does not require immediate SSH auth migration; that remains Phase C (`UA_SSH_AUTH_MODE`).

## Remaining Verification to Run on VPS
1. Deploy once with `UA_TAILNET_STAGING_MODE=required`.
2. Confirm `tailscale serve status` lists both configured HTTPS ports.
3. Validate tailnet-only access from an authorized tailnet client to:
- UI staging endpoint
- API staging endpoint
4. Confirm public endpoints are unaffected.

## Linkage
- Plan source: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/63_Tailnet_First_DevOps_Profile_And_Staging_Workflow_2026-02-21.md`
- Phase A record: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/66_Tailnet_First_DevOps_Phase_A_Implementation_2026-02-22.md`
