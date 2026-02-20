# 56 VP Testing And Workproduct Tracking Plan (2026-02-20)

## Purpose
Define and execute a reliable test strategy for external primary agents (VPs), then standardize where VP mission outputs and logs live so Simone-to-VP workflows are observable and sync-safe between VPS and local.

## Scope
1. VP runtime correctness.
2. Simone control-plane to VP data-plane contract health.
3. UI visibility of VP mission lifecycle.
4. Durable workproduct and log discoverability.
5. Completion-based sync compatibility for VP mission artifacts.

## Immediate Findings
1. We had stale `vp_sessions` and `vp_missions` rows showing old `active/running` state in the dashboard with no worker actually running.
2. Existing tests already covered dispatch/list/cancel endpoints, but did not execute an end-to-end general VP mission worker flow that writes a workproduct.
3. UI reflected DB status directly and needed stale-state hardening.

## Test Strategy

### A. Unit Tests (DB + Dispatcher + Guardrails)
1. Mission queue/claim/finalize semantics.
2. Cancel request behavior.
3. CODIE path guardrail enforcement.
4. Idempotency-key mission reuse.

Status: already present and should remain required in CI.

### B. Integration Tests (Gateway + Worker + APIs)
1. Dispatch general VP mission through `/api/v1/ops/vp/missions/dispatch`.
2. Run a VP worker tick and complete the mission.
3. Verify:
   - mission state transitions to `completed`,
   - VP events include `vp.mission.started` and `vp.mission.completed`,
   - workproduct file is created in mission workspace,
   - metrics endpoint reflects completion.

Status: implemented test case in `tests/gateway/test_ops_api.py`.

### C. UI Contract Tests (API-level plus optional Playwright)
1. Verify dashboard-facing APIs return expected mission/session/event payloads.
2. Optional Playwright scenario for:
   - dispatch mission,
   - poll until completion,
   - confirm mission row and result reference visible.

Status: API-level covered now; Playwright scenario recommended next.

## Implemented Test Case
`test_ops_vp_general_worker_execution_and_workproduct_tracking` now validates:
1. General VP mission dispatch.
2. Worker execution via a deterministic fake VP client.
3. Workproduct creation at `workspace://.../work_products/summary.md`.
4. Metrics/event contract continuity for dashboard consumption.

## VP Workproduct And Log Tracking Standard

### Canonical Layout
For each VP mission:
1. Workspace root:
   - local dev: `AGENT_RUN_WORKSPACES/vp_<name>_primary_external/<mission_id>/`
   - VPS: `/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_<name>_primary_external/<mission_id>/`
2. Required mission artifacts:
   - `work_products/` (primary deliverables)
   - `run.log` (worker execution log stream)
   - `mission_receipt.json` (recommended next: structured completion metadata)

### Result Reference Contract
`vp_missions.result_ref` must point to workspace URI:
`workspace://<absolute_or_resolved_mission_path>`

### Where To Inspect
1. Mission ledger:
   - `runtime_state.db` tables: `vp_missions`, `vp_events`, `vp_sessions`
2. Files:
   - mission workspace directory resolved from `result_ref`
3. UI:
   - Dashboard external primary agent section
   - Storage explorer for session/workspace paths

## Sync Compatibility Requirements
To ensure VPS→local mirror preserves VP outputs:
1. VP mission workspaces must stay under canonical `AGENT_RUN_WORKSPACES`.
2. Completion marker policy should include VP missions:
   - recommended next: write `sync_ready.json` in mission directory when mission finalizes.
3. Sync status endpoint should count VP mission-ready directories in backlog metrics.

## Operational Runbook (Manual)
1. Dispatch mission via dashboard or API.
2. Confirm mission reaches `running` then `completed`.
3. Open `result_ref` workspace path.
4. Validate `work_products/` contains expected deliverables.
5. Verify mirrored local copy after sync.

## Next Implementation Steps
1. Add `mission_receipt.json` generation in worker finalize path.
2. Add VP mission completion marker (`sync_ready.json`) at finalize.
3. Add dashboard link action: open `result_ref` path in Storage Explorer.
4. Add Playwright smoke test for dashboard VP dispatch→completion path.
5. Add stale-state reconciliation job (startup or periodic) for orphaned `running` missions.

## Acceptance Criteria
1. No false “active worker” display when no fresh heartbeat/session lease exists.
2. General VP mission test passes with deterministic workproduct creation.
3. Result reference always points to a real mission workspace.
4. VP workproducts are discoverable both on VPS and in local mirror after sync.
