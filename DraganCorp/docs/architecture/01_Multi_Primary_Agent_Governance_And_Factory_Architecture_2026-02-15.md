# 01. Multi-Primary Agent Governance and Factory Architecture (2026-02-15)

This document defines a phased architecture where Simone remains the core control plane while mission-specific cloned UA factories execute autonomous long-running work and report back through a governed communication layer.

## 0) Executive decisions (current)

1. **Simone is the core COO/control plane, not a factory runtime.**
2. **v1 user interface remains Simone-only** for CODER and all VP/factory pathways.
3. **CODER v1 is pragmatic**: coding assistance + coding automation, not a full Claude Code replacement product.
4. **Persistent VP sessions come before factories**: build CODER as a session-persistent primary runtime lane first.
5. **Factory model uses cloned UA deployments** (same skeleton, mission overlays), activated only when isolation/autonomy needs justify it.
6. **Adopt phased hybrid rollout** to minimize brittleness and maintenance cost.

---

## 1) Current-state reality check

### 1.1 What exists now (usable building blocks)

- Simone already operates as an orchestration-first conductor model with specialist delegation.
- Prompt strategy already routes repo implementation to `code-writer` for code changes.
- Gateway layer already supports:
  - in-process execution sessions, and
  - external gateway connectivity via HTTP/WebSocket client abstraction.

### 1.2 Known constraints

- In-process execution is currently serialized behind an execution lock to prevent cross-session contamination; this protects correctness but limits high-concurrency autonomous workloads in one runtime.
- Existing model is strong for coordinated tasks but does not yet define first-class, policy-governed, multi-instance factory operations.
- In CLI flow, `--resume` now prefers checkpoint-context injection and treats provider-session resume as fallback in that path; this means ad-hoc CLI resume semantics are not the right anchor for durable VP runtime persistence.

### 1.3 Implication

The architecture should treat core Simone runtime as a control plane, implement session-persistent VP lanes via gateway/session registry first, and externalize to clones only when long-duration parallel autonomy requires stronger isolation.

---

## 2) Feasibility verdict

## Verdict: **Yes, feasible now with phased controls.**

### 2.1 Distinguish execution models clearly

1. **Sub-agents (existing):** ephemeral specialists inside a single runtime turn/flow.
2. **In-core primary-role (new in v1):** CODER role with stronger autonomy window and reporting semantics, still under Simone control plane.
3. **Cross-deployment factory primaries (v2):** separate UA clone instances with mission-specific overlays and communication contracts.

### 2.2 Why this is practical

- Reuses existing orchestration, delegation, and gateway abstractions.
- Avoids premature infrastructure burden.
- Preserves backward compatibility and current production behavior while validating mission patterns.

---

## 3) Target operating model (CEO -> Simone COO -> VP and Factory lanes)

## 3.1 Role boundaries

- **User/CEO:** sets strategic intent, priorities, constraints.
- **Simone (COO/control plane):** planning, prioritization, risk/safety policy, delegation decisions, approval gating, final synthesis.
- **CODER VP runtime (v1/v1.5):** implementation and coding automation under bounded mission contracts, with persistent session continuity.
- **Factory clones (v2+):** autonomous long-running mission workers with periodic status and deliverable callbacks.

## 3.2 Interaction rule

- Default rule: all user interaction flows through Simone.
- Factory sessions are headless by default.
- Optional direct factory interaction is deferred to a future, feature-flagged escape hatch.

## 3.3 Escalation and return semantics

- Every delegated mission has:
  - mission_id,
  - scope + success criteria,
  - autonomy budget,
  - report cadence,
  - stop/kill signal path,
  - explicit return-to-Simone condition.

---

## 4) Control plane and data plane design

## 4.1 Control plane (Simone core)

Components:

1. **Mission classifier**
   - decides: in-core CODER VP runtime vs external factory clone lane.
2. **Dispatch coordinator**
   - submits missions, tracks state transitions.
3. **VP session registry**
   - maps VP identity -> runtime endpoint -> stable session/workspace identifiers.
4. **Concurrency governor**
   - enforces lane quotas against API subscription limits.
5. **Policy gate**
   - approvals for destructive/externalized actions.
6. **Audit + provenance ledger**
   - immutable mission event trail (who delegated what, when, why, with what artifact).

## 4.2 Data plane

1. **In-core lane**
   - CODER VP session under core gateway-managed lifecycle.
2. **Shared-VPS multi-runtime lane**
   - separate UA runtime processes (same host/skeleton), each with own session namespace and mission profile.
3. **Factory lane**
   - external cloned UA instances processing autonomous missions.

## 4.4 Persistence guidance

Use **gateway-managed session lifecycle + explicit VP session registry** as the persistence anchor.

Do not anchor VP persistence on ad-hoc CLI `--resume` semantics; use deterministic runtime/session identity, workspace continuity, and mission ledgering.

## 4.3 Inter-instance communication contract (v1 spec target)

Minimum message envelope:

- `mission_id`
- `source_instance_id`
- `target_instance_id`
- `mission_type`
- `objective`
- `constraints`
- `budget` (time, call quota, cost cap)
- `heartbeat_interval`
- `status`
- `artifact_refs`
- `trace_id`
- `idempotency_key`
- `timestamp`

Required event types:

- `mission.accepted`
- `mission.progress`
- `mission.blocked`
- `mission.artifact`
- `mission.completed`
- `mission.failed`
- `mission.cancelled`

---

## 5) CODER primary design (pragmatic v1)

## 5.1 Objective

Provide high-leverage coding support in two modes:

1. **Runtime assist mode** (interactive coding help while user works)
2. **Automation mode** (bounded coding task execution, test/run/report cycle)

## 5.2 Non-goal

Do not attempt to replicate full standalone Claude Code product behavior in v1.

## 5.3 CODER mission contract

- Inputs: repo context, task objective, constraints, acceptance criteria.
- Behavior: minimal diffs, tests where applicable, retry with real changes, explicit blockers.
- Outputs: diff summary, test results, artifact paths, next-step recommendation.

## 5.4 Compatibility requirement

- Preserve current `code-writer` delegation path for continuity and fallback.
- Introduce primary-role CODER as orchestration semantics first (policy + missioning), not as a broad runtime rewrite.

---

## 6) Prompt strategy review and structure

## 6.1 Recommended split

1. **Identity/style layer**
   - voice and posture (clear, direct, operator-grade).
2. **Operational policy layer**
   - safety, approvals, tool discipline, test expectations, escalation behavior.
3. **Task overlays**
   - coding-generalist default,
   - optional frontend excellence overlay,
   - future overlays for mission-specific factories.

## 6.2 Practical guidance

- Keep CODER default as coding-generalist for maximum utility.
- Activate frontend-heavy overlay only when UI/UX deliverable is central.
- Keep factory mission prompts concise and contract-driven to reduce drift in long-running autonomous jobs.

---

## 7) Migration plan with zero functional regression

## Phase A: v1 persistent CODER VP session (no factories)

1. Define CODER VP mission schema + lifecycle states.
2. Introduce VP session registry (stable VP ID, session ID, workspace, status).
3. Route coding intents from Simone to CODER VP runtime contract.
4. Keep `code-writer` fallback path unchanged.
5. Add observability fields (mission_id, trace_id, budget usage, vp_id).

## Phase B: v1.5 shared-VPS multi-runtime VP lanes

1. Run CODER VP in dedicated runtime process on same VPS (separate service/port/session namespace).
2. Validate bidirectional control channel (dispatch + callbacks + kill signal).
3. Enforce lane quotas across shared API subscription budgets.
4. Measure reliability, queue latency, and maintenance overhead.

## Phase C: v2 clone-ready template

1. Define clone profile packaging:
   - shared UA skeleton,
   - mission overlay config,
   - agent/skill pack toggle set,
   - environment profile.
2. Create standard bootstrap/deploy checklist.
3. Add health + heartbeat contract validation.

## Phase D: v2+ first autonomous factory pilot

1. Launch one mission-specific clone.
2. Integrate communication callbacks to Simone.
3. Measure reliability, autonomy quality, and ops overhead.
4. Expand only after pilot success gates are met.

Rollback principle: default to Simone-only + in-core execution if factory health/quality SLOs regress.

---

## 8) Risk register and safeguards

1. **Runaway autonomy**
   - Safeguard: hard autonomy window, max iteration count, mandatory heartbeat.
2. **Cross-instance duplicate actions**
   - Safeguard: idempotency keys and action dedupe ledger.
3. **Memory inconsistency/drift**
   - Safeguard: source-of-truth ownership rules + merge/reconciliation policy.
4. **API concurrency starvation**
   - Safeguard: lane-based quota governor with preemption rules.
5. **Operational complexity creep**
   - Safeguard: shared template + overlay model, no unmanaged forks.
6. **VP session drift or orphaning**
   - Safeguard: heartbeat + lease expiration + automatic reassignment/recovery rules.

---

## 9) Decision matrix (concurrency-aware)

Scoring: 1 (poor) to 5 (strong)

| Option | Description | Time-to-value | Reliability | Concurrency scaling | Maintenance cost | Governance clarity | Total |
|---|---|---:|---:|---:|---:|---:|---:|
| A | In-core single-runtime only | 5 | 4 | 2 | 4 | 5 | 20 |
| B | Shared-VPS multi-runtime VP lanes (no clones yet) | 4 | 5 | 4 | 4 | 5 | 22 |
| C | Clone-first only | 2 | 3 | 5 | 2 | 3 | 15 |
| D | **Hybrid phased (recommended)** (A -> B -> clones when needed) | 4 | 5 | 5 | 4 | 5 | **23** |

Recommendation: **Option D (hybrid phased)**.

---

## 10) API concurrency and capacity strategy

## 10.1 Lane model

1. **Core lane (reserved):** Simone and user-interactive workflows.
2. **VP lane (pooled):** persistent VP runtime sessions (e.g., CODER).
3. **Factory lane (elastic):** autonomous clone missions when enabled.
4. **Burst borrowing:** lower-priority lanes borrow idle capacity only under policy threshold.

## 10.2 Scheduling policy

- Priority classes:
  1. user-interactive requests,
  2. deadline-bound missions,
  3. background autonomy missions.
- Queue discipline:
  - deadline-aware + fairness cap per mission.
- Backpressure:
  - when saturated, defer low-priority autonomy and maintain core responsiveness.

## 10.3 Capacity triggers

Promote mission from in-core to shared-VPS VP runtime lane when:

- repeated queue pressure in core lane,
- mission duration regularly exceeds interactive thresholds,
- mission benefits from persistent specialized context.

Promote mission from VP runtime lane to factory lane when:

- queue wait exceeds threshold over sustained window,
- mission runtime repeatedly exceeds VP lane SLO,
- mission requires strong isolation/autonomy for multi-day execution,
- mission requires materially different agent/skill profile and lifecycle controls.

---

## 11) Concrete next actions

1. Use `DraganCorp/docs/operations/00_DraganCorp_Program_Control_Center.md` as the single source of truth for status/scope/changes/lessons.
2. Execute `DraganCorp/docs/operations/02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md` as the detailed Phase A blueprint.
3. Implement VP session registry spec and ownership rules in `DraganCorp/specs/`.
4. Define CODER VP runtime contract (dispatch, callback, kill, recovery).
5. Produce shared-VPS multi-runtime runbook in `DraganCorp/docs/operations/`.
6. Update ADR-001 with explicit A -> B -> clones graduation criteria.
7. Add pilot scorecards for both VP runtime phase and later factory phase.

---

## Appendix A: Memory provenance visibility (product idea)

Add an optional response-level provenance line in future UI/API responses:

- which memory entries were used,
- confidence/relevance score,
- quick actions: keep / edit / ignore.

Goal: increase trust, speed correction of stale memory, and improve operator awareness.
