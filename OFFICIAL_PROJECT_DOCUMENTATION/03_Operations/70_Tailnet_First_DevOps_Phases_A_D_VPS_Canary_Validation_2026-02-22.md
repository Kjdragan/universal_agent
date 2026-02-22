# 70. Tailnet-First DevOps Phases A-D VPS Canary Validation (2026-02-22)

## Summary
This document records VPS canary validation after implementing Tailnet-first DevOps Phases A-D (host/preflight, staging setup path, SSH auth mode switch, and runbook updates).

Date: 2026-02-22  
Validation window: ~03:50 UTC  
Status: Partially validated; one external tailnet policy blocker remains

## Scope Validated
1. Deployment script behavior with new tailnet preflight and VP checks.
2. Runtime service stability after deploy.
3. VP worker/session readiness.
4. Local port mapping for staging checks.
5. Tailnet staging setup feasibility via `tailscale serve`.

## Commands and Evidence

### 1. Deploy attempt (local -> VPS)
Command executed:
- `UA_VPS_HOST='root@100.106.113.93' UA_SSH_AUTH_MODE=keys ./scripts/deploy_vps.sh`

Observed:
1. Tailnet preflight passed (`tailscale status` + ping check).
2. Sync/dependency/build phases completed (including web-ui production build).
3. VP worker services installed and started.
4. Deploy script exited early at service-status gate when one unit briefly reported `activating` (systemd non-zero on `is-active` for transitional state).

Interpretation:
- This was a transient gate issue, not a functional runtime outage.

### 2. Post-deploy service steady-state
Command executed on VPS via SSH:
- service status probe for gateway/api/webui/telegram and both VP workers.

Observed:
1. `universal-agent-gateway=active`
2. `universal-agent-api=active`
3. `universal-agent-webui=active`
4. `universal-agent-telegram=active`
5. `universal-agent-vp-worker@vp.general.primary=active`
6. `universal-agent-vp-worker@vp.coder.primary=active`

### 3. VP session readiness
Command executed:
- query `AGENT_RUN_WORKSPACES/vp_state.db` for `vp_sessions` rows.

Observed:
1. `vp.general.primary` session row present and `active`.
2. `vp.coder.primary` session row present and `active`.

### 4. Public endpoint health
Command executed:
- `https://api.clearspringcg.com/api/v1/health`
- `https://app.clearspringcg.com/`

Observed:
1. API health HTTP `200` with healthy JSON payload.
2. App endpoint HTTP `200`.

### 5. Staging local target mapping check (VPS localhost)
Command executed:
- local port probes for:
  - `127.0.0.1:8001/`
  - `127.0.0.1:8001/api/v1/health`
  - `127.0.0.1:8002/`
  - `127.0.0.1:8002/api/v1/health`

Observed:
1. `8001/` returned `200`, but `8001/api/v1/health` returned `404`.
2. `8002/api/v1/health` returned `200`.

Action taken:
1. Updated staging script default API target from `127.0.0.1:8001` to `127.0.0.1:8002`:
- `scripts/configure_tailnet_staging.sh`
2. Updated Phase B documentation to match:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/67_Tailnet_First_DevOps_Phase_B_Implementation_2026-02-22.md`

### 6. Tailnet staging setup blocker
Command executed:
- direct `tailscale serve --yes --bg ...` on VPS.

Observed:
1. Tailscale returned:
- `Serve is not enabled on your tailnet.`
2. `tailscale serve status` reported `No serve config`.

Interpretation:
- Phase B script path is correct, but staging cannot be activated until tailnet admin policy enables Serve for this node/tailnet.

## Additional Hardening Applied During Canary
1. `scripts/configure_tailnet_staging.sh` now emits explicit policy-blocker error when Serve is disabled.
2. This makes deploy-time staging diagnostics actionable and avoids ambiguous failures.
3. `scripts/deploy_vps.sh` now uses bounded retries for service activation after restart.
4. This avoids false-negative deploy failures caused by short-lived `activating` transitions.

## Validation Outcome Matrix
1. Phase A (MagicDNS + preflight): PASS (implemented and exercised).
2. Phase B (staging script and deploy integration): PASS in code path, BLOCKED operationally by tailnet Serve policy.
3. Phase C (SSH auth mode): PASS in script behavior/syntax; canary deploy executed in `keys` mode.
4. Phase D (runbooks/source-of-truth): PASS (docs updated and aligned).

## Remaining Actions to Reach Full Operational PASS
1. Enable Tailscale Serve in tailnet admin for VPS node.
2. Re-run on VPS:
- `bash scripts/configure_tailnet_staging.sh --ensure`
- `bash scripts/configure_tailnet_staging.sh --verify-only`
- `tailscale serve status`
3. Re-run deploy with strict staging gate:
- `UA_TAILNET_STAGING_MODE=required ./scripts/deploy_vps.sh`
4. Optional deploy-script refinement:
- tolerate brief `activating` status with bounded retry before hard fail.

## Linkage
1. Plan: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/63_Tailnet_First_DevOps_Profile_And_Staging_Workflow_2026-02-21.md`
2. Phase A: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/66_Tailnet_First_DevOps_Phase_A_Implementation_2026-02-22.md`
3. Phase B: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/67_Tailnet_First_DevOps_Phase_B_Implementation_2026-02-22.md`
4. Phase C: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/68_Tailnet_First_DevOps_Phase_C_SSH_Auth_Mode_Implementation_2026-02-22.md`
5. Phase D: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/69_Tailnet_First_DevOps_Phase_D_Runbook_And_Source_Of_Truth_Update_2026-02-22.md`
