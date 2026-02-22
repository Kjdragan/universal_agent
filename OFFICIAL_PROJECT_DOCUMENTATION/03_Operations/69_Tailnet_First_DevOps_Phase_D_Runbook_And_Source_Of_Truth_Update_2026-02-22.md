# 69. Tailnet-First DevOps Phase D Runbook and Source-of-Truth Update (2026-02-22)

## Summary
This document records Phase D completion from the Tailnet-first DevOps plan (`63`): runbook updates and explicit source-of-truth operating discipline.

Date: 2026-02-22  
Status: Implemented (documentation and operator workflow)

## Scope Implemented
Phase D targets from `63`:
1. Update runbooks to reflect tailnet-first operations.
2. Document one canonical operator workflow.
3. Add explicit source-of-truth rule per validation pass.

## Updated Runbooks
1. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/27_Deployment_Runbook_2026-02-12.md`
2. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/46_Running_The_Agent.md`
3. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/20_VPS_Daily_Ops_Quickstart_2026-02-11.md`

## Key Documentation Changes

### 1. Host standard and auth mode clarity
1. Replaced operational host references with MagicDNS canonical host:
- `root@srv1360701.taildcc090.ts.net`
2. Added auth mode guidance:
- `UA_SSH_AUTH_MODE=keys|tailscale_ssh`

### 2. Deployment runbook hardening notes
1. Added deploy script behavior for:
- tailnet preflight checks
- tailnet staging setup/post-check behavior
2. Documented strict/auto/disabled staging mode behavior through `UA_TAILNET_STAGING_MODE`.

### 3. Daily ops quickstart additions
1. Added tailnet staging verification quick-check commands.
2. Added source-of-truth lane rule for debugging/acceptance conclusions.
3. Updated sync and timer command examples to MagicDNS host.

### 4. Running guide additions
1. Updated mirror/sync examples to MagicDNS host.
2. Added SSH auth mode notes for sync/deploy tooling.
3. Added dedicated section for tailnet-only staging checks.
4. Added explicit source-of-truth discipline section.

## Source-of-Truth Rule (Canonical)
For any operational claim, verification statement, or acceptance note:
1. Label the runtime lane used:
- local runtime
- VPS runtime
- public smoke
2. Avoid mixed-lane conclusions unless lane differences are explicitly called out.

## Linkage to Prior Phases
1. Plan: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/63_Tailnet_First_DevOps_Profile_And_Staging_Workflow_2026-02-21.md`
2. Phase A: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/66_Tailnet_First_DevOps_Phase_A_Implementation_2026-02-22.md`
3. Phase B: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/67_Tailnet_First_DevOps_Phase_B_Implementation_2026-02-22.md`
4. Phase C: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/68_Tailnet_First_DevOps_Phase_C_SSH_Auth_Mode_Implementation_2026-02-22.md`

## Operational Status After Phase D
1. Phase A (host + preflight): implemented.
2. Phase B (tailnet staging setup path): implemented.
3. Phase C (SSH auth mode switch): implemented.
4. Phase D (runbook/workflow updates): implemented.

Remaining work is operational validation/canary execution on VPS, not architecture or script-gap work.
