# UA Gateway Refactor — Outstanding Work (Master Tracker)

**Owner:** Handoff  
**Last updated:** 2026-01-24  
**Purpose:** Single source of truth for remaining stages, open items, and exit criteria.

---

## Current Status Snapshot
- **Stage 1 (Dependency Hardening):** ✅ Complete
- **Stage 2 (Event Stream Normalization):** ✅ Complete (parity sign-off documented 2026-01-24)
- **Stage 3 (Gateway API In‑Process):** ✅ Complete (default + banner policy implemented 2026-01-24)
- **Stage 4 (Gateway Externalization):** ✅ Complete (server + client + CLI integration implemented 2026-01-24)
- **Stage 5 (URW as Meta‑Client):** ✅ Complete (GatewayURWAdapter + HarnessOrchestrator gateway mode implemented 2026-01-24)
- **Stage 6 (Worker Pool + Lease Durability):** ✅ Complete (WorkerPoolManager + lease coordination implemented 2026-01-24)

---

## Stage 2 — Event Stream Normalization (Finalize)
**Goal:** CLI output parity when rendered from gateway event stream.

### Completed Evidence
- Parity diffs captured:
  - `Refactor_Workspace/stage2_validation/cli_vs_gateway_listdir_fix.diff`
  - `Refactor_Workspace/stage2_validation/cli_vs_gateway_write_read.diff`
  - `Refactor_Workspace/stage2_validation/cli_vs_gateway_search_chain_fix4.diff`
  - `Refactor_Workspace/stage2_validation/cli_vs_gateway_combo_chain.diff`
  - `Refactor_Workspace/stage2_validation/cli_vs_gateway_edit_chain.diff`
- Observer save lines now appear in gateway output and Write/Read paths normalize to gateway workspace.

### Remaining Items
- **Formal parity sign‑off**: Document that remaining deltas are expected (session/trace IDs, gateway banner, model variance).
- **Policy warning for `Edit`**: Decide whether to add `Edit` to tool policy or accept warning.
- **Smoke tests doc update**: Add Stage 2 parity runs to `ua_gateway_smoke_tests.md`.

**Exit criteria:** Written sign‑off in progress log and smoke test doc, deltas accepted or resolved.

---

## Stage 3 — Gateway API (In‑Process)
**Goal:** Make gateway the default path in dev mode, keep CLI behavior intact.

### Completed Evidence
- Gateway ledger integration fixed (pre‑tool step creation + ledger rows).
- Gateway job‑mode enabled; `job_completion_gateway_*` summaries created.
- Dev‑mode gateway default trial run:
  - `Refactor_Workspace/stage2_validation/cli_gateway_default_trial.log`
  - `Refactor_Workspace/stage2_validation/cli_vs_gateway_default_trial.diff`

### Remaining Items
- **Decide gateway default behavior** (dev‑mode flag flip? CLI default in prod?)
- **Output banner policy**: keep or gate “Gateway preview enabled” banner.
- **Update plan/progress** with a clear Stage 3 exit criteria and sign‑off.

**Exit criteria:** Gateway default path in dev with documented deltas, explicit decision on prod default.

---

## Stage 4 — Gateway Externalization
**Goal:** HTTP/WebSocket gateway parity with in‑process.

### Remaining Items
- Implement gateway server endpoints (HTTP/WS).
- Add compatibility layer for session management.
- Verify parity with existing API `AgentBridge`.
- Update smoke tests for external gateway path.

**Exit criteria:** External gateway matches in‑process behavior; CLI default remains in‑process.

---

## Stage 5 — URW as Meta‑Client
**Goal:** URW routes through Gateway API instead of `process_turn`.

### Remaining Items
- Update URW adapter to call Gateway and subscribe to events.
- Preserve per‑phase workspace binding semantics.
- Decide on dedicated URW phase events.

**Exit criteria:** URW workflows succeed through Gateway with no regressions.

---

## Stage 6 — Worker Pool + Lease Durability
**Goal:** Distributed execution with durable leases.

### Remaining Items
- Implement lease acquisition/heartbeat in workers.
- Add worker pool config and scheduling.
- Validate resume across workers.

**Exit criteria:** Durable runs resume across workers without data loss.

---

## Risk / Open Decisions
- **Dual Composio sessions**: currently accepted for complex gateway flows; revisit before externalization.
- **Edit tool policy warning**: decide whether to whitelist or document as accepted warning.
- **Gateway default**: dev‑mode default vs prod default.

---

## Files to Reference
- Plan: `Refactor_Workspace/ua_gateway_refactor_plan.md`
- Progress: `Refactor_Workspace/ua_gateway_refactor_progress.md`
- Guardrails: `Refactor_Workspace/ua_gateway_guardrails_checklist.md`
- Smoke tests: `Refactor_Workspace/ua_gateway_smoke_tests.md`

