# 65. VP Independent Mission Model, Simone Integration, and Tailnet Next Focus (2026-02-22)

## 1. Purpose
This document memorializes the completed VP-independence workstream and clarifies how external VPs differ from traditional UA sub-agents. It also records the immediate next focus area for VPS/Tailscale integration work.

Date: 2026-02-22  
Status: Implemented (VP core path), Next phase identified (Tailnet DevOps)

## 2. Original Goal
Primary objective:
1. Establish an independent coder agent (CODIE) that can operate as an external primary VP lane.
2. Add a second independent general-purpose VP lane that Simone can delegate autonomous missions to.
3. Keep Simone as control plane while VPs run execution work in separate mission sessions/workspaces.

## 3. What Was Implemented

### 3.1 External VP architecture and mission control
1. Dedicated external VP ledger (`vp_state.db`) separated from core runtime state.
2. Tool-first VP control contract implemented and exposed:
- `vp_dispatch_mission`
- `vp_get_mission`
- `vp_list_missions`
- `vp_wait_mission`
- `vp_cancel_mission`
- `vp_read_result_artifacts`
3. Mission lifecycle bridge from VP events back into originating Simone session.
4. CODIE and General VP parity at mission-control interface via `vp_id`.

References:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/58_VP_Tool_First_Orchestration_And_Dedicated_VP_DB_Implementation_Plan_2026-02-21.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/59_VP_Tool_First_Orchestration_Implementation_Completion_And_Deployment_2026-02-21.md`

### 3.2 Strict explicit VP routing and deployment hardening
1. Explicit intent routing hardened so phrases like “use General VP/DP” route deterministically to external VP dispatch.
2. Strict external policy for explicit VP turns avoids silent fallback to non-VP execution paths.
3. VP workers installed and managed as always-on systemd services in VPS deploy flow.

Reference:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/64_VP_Independence_Deployment_And_Strict_Explicit_Routing_Implementation_2026-02-21.md`

### 3.3 Completion-handoff and artifact visibility refinements (this cycle)
1. Completion events now include discovered mission output files, not only metadata markers.
2. Simone completion notices now surface VP summary plus concrete artifact paths.
3. UI now categorizes mission outputs into:
- `Primary work product`
- `Supporting artifact`
4. Receipt/sync markers remain visible for operations and sync integrity.

Code touchpoints implemented in this cycle:
- `src/universal_agent/vp/worker_loop.py`
- `web-ui/lib/store.ts`

## 4. How VPs Differ from Standard UA Sub-Agents

### 4.1 Execution model
1. VP lanes are external worker processes with independent mission queues and ledgers.
2. Traditional sub-agents are typically in-turn delegated execution inside Simone’s runtime flow.
3. VP missions can be queued and completed asynchronously while Simone continues conversational control-plane work.

### 4.2 State and observability model
1. VP mission/session/event records are durable and queryable via VP ops APIs/tools.
2. Mission lifecycle events are bridged back into Simone’s session, creating a control-plane narrative with execution-plane truth.
3. Mission artifacts are anchored to mission-scoped workspaces and referenced via `result_ref`.

### 4.3 Guardrail model
1. Explicit VP intent can be made strict, preventing silent fallback.
2. CODIE retains path guardrails for UA-internal safety.
3. General VP remains broad-task capable but follows same mission-control contract.

## 5. Lessons Learned
1. Prompt-only delegation language is insufficient for deterministic VP routing; control-plane enforcement is required.
2. Without first-class internal VP tools, agents naturally drift into ad-hoc shell/API probing.
3. Mission completion UX must include concrete work-product paths, not just control metadata (`mission_receipt`, `sync_ready`).
4. Distinguishing “deliverable” vs “supporting diagnostics” materially improves operator trust and usability.
5. VP correctness must be validated on actual VPS runtime behavior, not solely on mirrored local artifacts.

## 6. Remaining Gaps and Risks
1. Credential/API-access strategy for independent VP lanes requires formalization:
- shared vs lane-specific credentials
- least-privilege scope boundaries
- rotation and audit practices
2. Further artifact prioritization may be needed for complex runs with many generated files.
3. Continue monitoring for regressions where explicit VP intent could be bypassed by alternate delegation patterns.

## 7. Where We Left Off on VPS + Tailscale Integration
The active implementation plan is:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/63_Tailnet_First_DevOps_Profile_And_Staging_Workflow_2026-02-21.md`

Current state from that plan:
1. Status is **Planned (not yet implemented)**.
2. Locked decisions already set:
- MagicDNS host standard
- tailnet-only staging endpoints
- phased Tailscale SSH rollout
- local-first + VPS gate workflow
3. Planned execution phases:
- Phase A: host canonicalization + tailnet preflight in deploy/sync scripts
- Phase B: tailnet-only staging routing (`tailscale serve`)
- Phase C: SSH auth mode switch (`keys` vs `tailscale_ssh`)
- Phase D: runbook updates and source-of-truth discipline

## 8. Recommended Next Implementation Sequence
1. Execute `63` Phase A first (script host canonicalization + preflight checks).
2. Then execute `63` Phase B (private tailnet staging path + verification script).
3. Keep VP-process verification as VPS-gated acceptance for any mission-behavior claims.
4. After Phase A/B, run a focused VP smoke on staging to verify:
- dispatch
- terminal lifecycle event bridge
- primary/supporting artifact categorization in chat UI

## 9. Acceptance Snapshot
As of 2026-02-22:
1. CODIE and General VP lanes operate as independent mission workers under Simone control-plane delegation.
2. Explicit VP delegation behavior is hardened with strict-path capability.
3. Completion feedback now includes actionable mission output references and categorized artifacts.
4. Tailnet-first DevOps integration remains the next major unimplemented operations track.
