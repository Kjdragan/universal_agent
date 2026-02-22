# 72. Tailnet SSH Auth Mode Canary Completion (2026-02-22)

## Summary
This document records successful canary completion for `UA_SSH_AUTH_MODE=tailscale_ssh` using strict deploy gates.

Date: 2026-02-22  
Status: PASS

## Objective
Validate that operational scripts and full deployment flow work end-to-end without SSH key injection when `UA_SSH_AUTH_MODE=tailscale_ssh`.

## Canary Commands
1. Non-destructive status probe:
- `UA_SSH_AUTH_MODE=tailscale_ssh UA_VPS_HOST='root@100.106.113.93' scripts/vpsctl.sh status all`
2. Sync status probe:
- `UA_SSH_AUTH_MODE=tailscale_ssh UA_REMOTE_SSH_HOST='root@100.106.113.93' UA_SKIP_TAILNET_PREFLIGHT=true scripts/sync_remote_workspaces.sh --status-json`
3. Strict deploy canary:
- `UA_VPS_HOST='root@100.106.113.93' UA_SSH_AUTH_MODE=tailscale_ssh UA_TAILNET_STAGING_MODE=required ./scripts/deploy_vps.sh`

## Initial Failure and Remediation
Initial strict deploy run failed at tailnet staging setup because UI/API local health checks ran too early after service restart (`HTTP 000` transient).

Remediation applied:
1. `scripts/configure_tailnet_staging.sh` now performs bounded health retries:
- `UA_TAILNET_STAGING_HEALTH_MAX_ATTEMPTS` (default `12`)
- `UA_TAILNET_STAGING_HEALTH_SLEEP_SECONDS` (default `5`)
2. Re-pushed script to VPS and re-ran strict deploy canary.

## Final PASS Evidence
From final strict deploy run:
1. Tailnet preflight passed.
2. Full web build completed.
3. Core services and VP workers reached `active`.
4. Tailnet staging setup passed under `required` mode.
5. VP session readiness checks passed.
6. Public health checks passed:
- `API=200`
- `APP=200`
7. Ops auth checks passed:
- `OPS_UNAUTH=401`
- `OPS_AUTH=200`
8. Deployment finished with `Deployment completed.`

## Conclusion
`UA_SSH_AUTH_MODE=tailscale_ssh` is now validated for canary use in this environment, including strict staging-gated deployment.

## Recommended Default Policy
1. Keep default as `UA_SSH_AUTH_MODE=keys` for conservative baseline.
2. Use `tailscale_ssh` where tailnet SSH policy is established and monitored.
3. Keep `UA_TAILNET_STAGING_MODE=required` for production deploy gates.

## Linkage
1. Phase C implementation: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/68_Tailnet_First_DevOps_Phase_C_SSH_Auth_Mode_Implementation_2026-02-22.md`
2. Final A-D closure: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/71_Tailnet_First_DevOps_Phases_A_D_Final_Closure_Validation_2026-02-22.md`
