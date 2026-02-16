# 06. Phase B Shared-VPS Multi-Runtime Operating Model and Meta Prompt

This document is the **Phase B equivalent** of the Phase A execution model.

It explains how to run Phase B work in a structured, evidence-first way, with explicit governance, verification gates, and documentation discipline.

---

## 1) What changes from Phase A to Phase B

Phase A focus:
- One persistent CODIE lane in-core (legacy identifier: CODER VP), with guarded rollout and fallback.

Phase B focus:
- Multiple VP runtime lanes on the same VPS (separate runtime processes / ports / session namespaces).
- Strong control-plane governance for dispatch, quotas, and failover.
- Capacity-aware scheduling and reliability under concurrent load.

In short: **Phase A proves the lane; Phase B scales the lane model safely.**

---

## 2) Canonical docs to keep in sync

For Phase B sessions, treat these as required:

1. `00_DraganCorp_Program_Control_Center.md`
   - Live status, decisions, lessons, governance.
2. `01_Multi_Primary_Agent_Governance_And_Factory_Architecture_2026-02-15.md`
   - Phase A -> Phase B -> clone architecture intent.
3. `02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md`
   - Baseline for contracts and acceptance continuity.
4. `03_Phase_A_CODER_VP_Observability_Playbook.md`
   - Operator query patterns (extend/adapt for multi-runtime lanes).
5. `04_Phase_A_Controlled_Rollout_Evidence_Log.md`
   - Historical rollout evidence baseline and promotion context.
6. `05_Phase_A_Execution_Operating_Model_and_Meta_Prompt.md`
   - Phase A methodology reference.

This file defines **Phase B operating behavior and prompt contract**.

---

## 3) Phase B core operating approach

### A) Treat each runtime lane as a productized unit
Every lane must have:
- stable identity,
- health/heartbeat,
- mission queue behavior,
- explicit quota/budget controls,
- deterministic failure handling.

### B) Keep control plane and data plane separate
- Simone/control plane decides what to run where.
- VP runtimes execute and report.

### C) Scale only with measurable safety
Do not increase concurrency/traffic without fresh evidence.

### D) Keep rollback always ready
Any lane can be drained or bypassed back to safe fallback path.

---

## 4) Phase B execution loop (detailed)

### Step 1: Scope + lane target definition
For each packet, define:
- targeted runtime lane(s),
- intended behavior change,
- expected quota/latency impact,
- rollback trigger.

### Step 2: Packetized implementation
Use 2-6 packets, each scoped to one change domain:
- control-plane routing policy,
- lane registration/health,
- quota governor,
- callback/reporting,
- observability/alerts.

### Step 3: Verification ladder
Run in order:
1. Unit tests (state transitions, policy rules, quota decisions).
2. Integration tests (dispatch to target lane, callback correctness, idempotency).
3. Concurrency tests (queue latency and starvation behavior under load).
4. Operational probes (heartbeat, fallback/recovery behavior, lane health endpoints).

### Step 4: Evidence capture
For every change that touches runtime behavior, capture:
- lane utilization/queue metrics,
- fallback/failure rates,
- p95/p99 latency,
- saturation behavior,
- recovery success after forced failure.

### Step 5: Documentation synchronization
Update in this order:
1. `00` status log,
2. `00` decision log (if governance/policy changed),
3. relevant observability/evidence docs,
4. `00` lessons.

### Step 6: Commit discipline
One logical change bundle per commit.

---

## 5) Phase B verification gates (must-pass)

### Gate B1: Lane identity and isolation
- No cross-lane state bleed.
- Session IDs/workspaces remain lane-consistent.

### Gate B2: Dispatch correctness
- Control plane routes to intended lane based on policy.
- Callbacks map to correct mission/session.

### Gate B3: Quota governance
- Lane quotas enforced.
- Priority classes honored (interactive > deadline > background).

### Gate B4: Recovery determinism
- Lane degradation triggers recovery/fallback correctly.
- Stale/failed runtime does not leave orphaned active missions.

### Gate B5: Observability completeness
- Required lane/mission events emitted and queryable.
- Operators can detect failures quickly.

### Gate B6: Documentation integrity
- Status/decisions/lessons reflect what actually shipped.

---

## 6) Dynamic documentation adherence (Phase B)

| Change type | Required doc updates |
|---|---|
| Runtime-lane topology/routing policy | `00` status + `00` decision log + architecture doc if model changed |
| Quota/priority/governor behavior | `00` status + decision log + observability playbook guidance |
| Concurrency/load evidence | evidence log section (or Phase B evidence file) + `00` status |
| Recovery/fallback semantics | `00` status + `00` decision log + lessons |

Rule: If operator behavior would change, documentation update is mandatory.

---

## 7) Phase B rollout and rollback model

### Promotion policy
Increase runtime-lane traffic only when:
- fallback/failure rates are healthy,
- queue latency is stable,
- no starvation of interactive lane,
- recovery drills pass.

### Rollback triggers (example)
- sustained fallback spike above policy threshold,
- sustained mission failure pattern,
- queue starvation for interactive traffic,
- heartbeat/health instability in one or more lanes.

### Rollback actions
1. Drain affected lane(s).
2. Re-route to stable lane or fallback path.
3. Capture incident snapshot.
4. Record decision + lesson.

---

## 8) Meta prompt for AI coder (Phase B)

```text
You are the implementation operator for DraganCorp Phase B (shared-VPS multi-runtime VP lanes).

Objective:
- [INSERT PHASE B OBJECTIVE]

Constraints:
- Small packetized changes only.
- No unrelated refactors.
- Preserve safe fallback path at all times.

Required process:
1) Read and honor canonical docs:
   - 00_DraganCorp_Program_Control_Center.md
   - 01_Multi_Primary_Agent_Governance_And_Factory_Architecture_2026-02-15.md
   - 02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md
   - 03_Phase_A_CODER_VP_Observability_Playbook.md
   - 04_Phase_A_Controlled_Rollout_Evidence_Log.md
   - 05_Phase_A_Execution_Operating_Model_and_Meta_Prompt.md
   - 06_Phase_B_Shared_VPS_Multi_Runtime_Operating_Model_and_Meta_Prompt.md
2) Propose 2-6 packets with explicit verification and rollback checkpoints.
3) For each packet:
   - implement,
   - run verification ladder,
   - report pass/fail with evidence.
4) Capture runtime evidence for lane behavior (latency, fallback/failure, queue pressure).
5) Update docs immediately (status/decision/lesson discipline).
6) Use scoped commits.

Must-pass gates:
- B1 lane identity/isolation
- B2 dispatch correctness
- B3 quota governance
- B4 recovery determinism
- B5 observability completeness
- B6 documentation integrity

Output every cycle:
- What changed
- Verification commands + outcomes
- Lane metrics/evidence captured
- Docs updated
- Risks + rollback readiness
```

---

## 9) Short meta prompt (fast mode)

```text
Follow the DraganCorp Phase B operating model: packetized changes, lane-aware verification, evidence-first scaling, explicit rollback triggers, and mandatory live doc sync in Program Control Center.
```

---

## 10) Session close checklist (Phase B)

- [ ] Lane behavior change validated in tests/probes.
- [ ] Concurrency/queue/fallback metrics captured.
- [ ] Recovery/rollback path verified or reaffirmed.
- [ ] `00` status updated.
- [ ] `00` decision log updated if policy changed.
- [ ] Lessons learned updated.
- [ ] Commit scope clean and auditable.

---

## 11) Success criteria for Phase B operating discipline

The process is working when:

1. Lane scaling decisions are evidence-based.
2. Regressions are caught before broad rollout.
3. Operators can quickly identify and recover degraded lanes.
4. Documentation stays aligned with runtime reality.
5. Future AI coders can continue work with low drift using the meta prompt.
