# 63_Tailnet_First_DevOps_Profile_And_Staging_Workflow_2026-02-21

## Summary
This document records the Tailnet-first DevOps plan so operations can proceed independently if VP process deployment is delayed.

Date: 2026-02-21  
Status: Planned (not yet implemented)

## Purpose
Standardize development and deployment mechanics across local and VPS environments using Tailscale as the private control plane, while preserving fast local iteration and deterministic VPS validation.

## Locked Decisions
1. Development workflow: Local-first + VPS gate.
2. Remote host standard: MagicDNS only (`srv1360701.taildcc090.ts.net`).
3. Staging access: Tailnet-only staging endpoints for integration checks.
4. SSH model: Phased Tailscale SSH rollout (not immediate cutover).
5. Public domains (`app.clearspringcg.com`, `api.clearspringcg.com`) remain final smoke only.

## Current-State Observations
1. Existing deploy/sync scripts already use Tailscale transport semantics (`100.x` address), but use IP-based defaults.
2. Sync tooling is robust (manifest skip, ready-marker gating, remote toggle), but mirrored files are not runtime parity.
3. Public app is currently overused for integration debugging; this increases ambiguity.
4. VP worker service readiness is a separate concern and must be validated on VPS runtime, not via local mirrors.

## Scope
### In Scope
1. Script host canonicalization to MagicDNS.
2. Tailnet preflight checks before deploy/sync operations.
3. Tailnet-only staging path for private integration validation.
4. Phased SSH auth migration option via Tailscale SSH.
5. Runbook updates for one canonical operator workflow.

### Out of Scope
1. Splitting repositories.
2. Multi-host orchestration.
3. Container/Kubernetes migration.
4. Replacing rsync sync architecture.

## Implementation Plan
### Phase A: Host and Preflight Hardening
1. Update defaults in:
   - `scripts/deploy_vps.sh`
   - `scripts/vpsctl.sh`
   - `scripts/sync_remote_workspaces.sh`
   - `scripts/pull_remote_workspaces_now.sh`
2. Canonical host: `root@srv1360701.taildcc090.ts.net`.
3. Add tailnet preflight:
   - verify `tailscale status` running
   - verify `tailscale ping srv1360701.taildcc090.ts.net`
   - fail fast with actionable message.

### Phase B: Tailnet-Only Staging
1. Add private staging routing via `tailscale serve` on VPS:
   - UI -> `127.0.0.1:3000`
   - API -> `127.0.0.1:8001`
2. Add setup/verification script:
   - `scripts/configure_tailnet_staging.sh`
3. Add deploy post-check for staging health.

### Phase C: SSH Auth Phased Rollout
1. Introduce auth mode switch:
   - `UA_SSH_AUTH_MODE=keys|tailscale_ssh`
2. Keep `keys` default initially.
3. Run canary in `tailscale_ssh`.
4. Cut over default after stable window; keep key-based break-glass fallback documented.

### Phase D: Documentation and Operator Workflow
1. Update runbooks:
   - `03_Operations/27_Deployment_Runbook_2026-02-12.md`
   - `03_Operations/46_Running_The_Agent.md`
   - `03_Operations/20_VPS_Daily_Ops_Quickstart_2026-02-11.md`
2. Add explicit source-of-truth rule per run:
   - local runtime, VPS runtime, or public smoke (never mixed conclusions).

## Test and Acceptance Criteria
1. MagicDNS-only scripts deploy and sync successfully.
2. Deploy/sync fail clearly when tailnet peer is offline.
3. Tailnet staging endpoint returns healthy UI/API responses.
4. Public endpoints remain unaffected.
5. `UA_SSH_AUTH_MODE=keys` and `tailscale_ssh` both validated in staged checks.

## Risks and Mitigations
1. MagicDNS resolution issues:
   - Mitigation: preflight + break-glass env override.
2. Tailscale serve misconfiguration:
   - Mitigation: idempotent setup script + deploy verification.
3. SSH cutover disruption:
   - Mitigation: phased rollout + immediate rollback switch.

## Operational Guidance
1. Keep sync toggle OFF by default.
2. Use sync mirror only for remote artifact inspection.
3. Require VPS gate validation for VP/process behavior.
4. Use public app only for final smoke acceptance.

## Dependencies
1. VP-process readiness effort remains primary track.
2. Tailnet-first DevOps can be executed independently if VP rollout is delayed.
