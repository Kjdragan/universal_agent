# 02. Phase A Detailed Implementation Plan: Persistent CODER VP Session

This plan defines the exact workstreams, contracts, test gates, and rollout controls needed to implement Phase A (persistent CODER VP session) with zero regression to current Simone-led UA behavior.

## 1) Phase A objective and constraints

## 1.1 Primary objective

Implement a CODER VP runtime lane with durable session continuity (session/workspace identity, resume/recovery semantics, mission tracking) while Simone remains the only user-facing orchestrator.

## 1.2 Hard constraints

1. No factories/clones in Phase A.
2. No disruption to current Simone orchestration flow.
3. Keep `code-writer` delegation as always-available fallback.
4. Default-off rollout behind feature flags.
5. All changes traceable in Program Control Center.

## 1.3 Non-goals

1. No new external deployment topology.
2. No direct user interaction with CODER VP runtime.
3. No broad prompt-system rewrite.
4. No multi-VP fleet rollout beyond CODER in this phase.

---

## 2) Current-state technical anchors

Phase A design aligns with current code reality:

1. Gateway supports create/resume session lifecycle.
2. In-process gateway execution is lock-serialized, requiring careful workload boundaries.
3. Gateway server already exposes session endpoints and runtime DB initialization.
4. Runtime durable DB already tracks runs/checkpoints/tool calls and can be extended for VP session/missions.

---

## 3) Deliverables (Phase A exit package)

1. **VP Session Registry Spec + Schema**
   - canonical schema for VP identity/session/workspace/status/lease.
2. **CODER VP Mission Contract**
   - dispatch, progress, completion, blocked, failed, cancelled semantics.
3. **Runtime Integration**
   - Simone routing path to CODER VP lane under flag.
4. **Recovery/Fallback Controls**
   - heartbeat + stale-session recovery + fallback to `code-writer`.
5. **Observability & SLO Dashboard Inputs**
   - logs/metrics/events for latency, recovery, fallback rates.
6. **Test Suite + Rollout Artifacts**
   - unit, integration, regression, and rollback playbook updates.

---

## 4) Workstream breakdown

## WS-A: Contracts, schema, and lifecycle definition

### Scope

Define machine-readable contracts before runtime changes.

### Tasks

1. Define `vp_id` namespace conventions (e.g., `vp.coder.primary`).
2. Define session lifecycle states:
   - `idle`, `active`, `paused`, `degraded`, `recovering`, `retired`.
3. Define mission lifecycle states:
   - `queued`, `running`, `blocked`, `completed`, `failed`, `cancelled`, `timed_out`.
4. Define ownership model:
   - Simone as control-plane owner, CODER VP as data-plane worker.
5. Extend specs:
   - `specs/mission-envelope-v1.md` (phase-A fields for vp_id/session linkage)
   - new `specs/vp-session-registry-v1.md`.

### Exit criteria

- lifecycle/state definitions approved and documented.
- all required fields are deterministic and versioned.

---

## WS-B: VP session registry implementation

### Scope

Create durable registry for VP session identity and health.

### Proposed runtime data model

1. `vp_sessions`
   - `vp_id` (pk)
   - `runtime_id`
   - `session_id`
   - `workspace_dir`
   - `status`
   - `lease_owner`
   - `lease_expires_at`
   - `last_heartbeat_at`
   - `last_error`
   - `created_at`, `updated_at`
2. `vp_missions`
   - `mission_id` (pk)
   - `vp_id`
   - `run_id`
   - `status`
   - `objective`
   - `budget_json`
   - `result_ref`
   - `started_at`, `completed_at`, `updated_at`
3. `vp_events`
   - `event_id` (pk)
   - `mission_id`
   - `vp_id`
   - `event_type`
   - `payload_json`
   - `emitted_at`

### Tasks

1. Add migration updates in durable migrations module.
2. Add state access functions (upsert session, heartbeat, lease renewal, mission update, event append).
3. Add lightweight registry service module (`vp/session_registry.py`) with typed APIs.
4. Add stale-session sweeper logic (lease expiry -> `degraded/recovering`).

### Exit criteria

- registry survives restart and supports deterministic resume/recovery lookups.

---

## WS-C: CODER VP runtime contract and adapter

### Scope

Implement controlled dispatch path from Simone to CODER VP session.

### Tasks

1. Build CODER VP adapter service (`vp/coder_runtime.py`):
   - `ensure_session(vp_id)`
   - `dispatch_mission(mission)`
   - `stream_progress(mission_id)`
   - `cancel_mission(mission_id)`
   - `recover_session(vp_id)`
2. Maintain workspace continuity per `vp_id`.
3. Ensure mission events are persisted to `vp_events`.
4. Attach trace metadata (`mission_id`, `vp_id`, `session_id`, `run_id`).

### Exit criteria

- CODER VP can execute sequential tasks with persistent context and stable identity.

---

## WS-D: Simone orchestration routing integration

### Scope

Allow Simone to route coding intents to CODER VP lane under explicit controls.

### Tasks

1. Add routing policy layer:
   - route by intent + workload profile + lane capacity.
2. Add feature flags:
   - `UA_ENABLE_CODER_VP`
   - `UA_CODER_VP_SHADOW_MODE`
   - `UA_CODER_VP_FORCE_FALLBACK`
3. Preserve fallback path:
   - failure/timeouts route to existing `code-writer` path.
4. Ensure response synthesis includes provenance:
   - “handled by Simone direct” vs “delegated to CODER VP”.

### Exit criteria

- no user-visible regression with flags off.
- safe routing with deterministic fallback with flags on.

---

## WS-E: Observability, controls, and SLOs

### Scope

Add instrumentation and operational controls needed for safe rollout.

### Tasks

1. Emit events:
   - `vp.session.created`, `vp.session.resumed`, `vp.session.degraded`,
   - `vp.mission.dispatched`, `vp.mission.completed`, `vp.mission.failed`, `vp.mission.fallback`.
2. Add SLO counters:
   - mission latency p50/p95,
   - fallback rate,
   - recovery success rate,
   - session orphan rate.
3. Add operational inspection endpoints/queries (internal only).
4. Add runbook section for “VP session recovery drill”.

### Exit criteria

- team can detect failures quickly and execute deterministic recovery.

---

## WS-F: Testing and validation

### Test matrix

1. **Unit tests**
   - registry CRUD and lease semantics,
   - mission state transitions,
   - fallback decision logic.
2. **Integration tests**
   - Simone -> CODER VP dispatch -> completion,
   - resume after restart,
   - stale lease recovery.
3. **Regression tests**
   - baseline Simone orchestration unchanged when flags off.
4. **Failure injection tests**
   - invalid session_id,
   - timeout,
   - partial callback/event loss,
   - forced fallback path.

### Exit criteria

- all critical path tests passing.
- rollback path validated in staging-like run.

---

## 5) Implementation sequencing (recommended)

1. **Milestone M1 (contracts + schema):** WS-A + schema subset of WS-B
2. **Milestone M2 (registry core):** WS-B service + persistence tests
3. **Milestone M3 (runtime adapter):** WS-C + integration harness
4. **Milestone M4 (routing + fallback):** WS-D + shadow mode
5. **Milestone M5 (observability + drills):** WS-E + WS-F + rollout gate review

---

## 6) Rollout strategy

## Stage 0: Dark launch (flags off)

- deploy code paths inactive; validate no regressions.

## Stage 1: Shadow mode

- CODER VP executes in parallel for selected tasks; Simone responses remain from existing path.
- compare outcomes and latency.

## Stage 2: Controlled activation

- enable routing for narrow coding-intent cohort.
- monitor fallback and recovery rates.

## Stage 3: Broad Phase A enablement

- enable by default for eligible coding intents, retaining fallback and kill switch.

Rollback: set `UA_CODER_VP_FORCE_FALLBACK=1` and route all to `code-writer` path.

---

## 7) Acceptance criteria (Phase A done)

1. CODER VP session persists across process restarts with deterministic resume behavior.
2. Simone can delegate coding missions with mission IDs and tracked status.
3. Fallback to `code-writer` is automatic on timeout/error and observable.
4. No major regressions in existing Simone orchestration flows.
5. Program Control Center is fully updated with implementation outcomes.

---

## 8) Documentation and governance requirements

Mandatory updates per implementation session:

1. Update checklist and status in `00_DraganCorp_Program_Control_Center.md`.
2. Record scope/design changes in Change Control Register.
3. Record implementation decisions and lessons learned.
4. Keep this plan current if execution sequence changes.

No undocumented change is considered accepted.

---

## 9) Suggested file-level implementation map (for coding phase)

- `src/universal_agent/durable/migrations.py` (new VP tables)
- `src/universal_agent/durable/state.py` (registry + mission/event state APIs)
- `src/universal_agent/vp/session_registry.py` (new)
- `src/universal_agent/vp/coder_runtime.py` (new)
- `src/universal_agent/gateway_server.py` (registry hooks/ops visibility)
- `src/universal_agent/main.py` or routing layer module (Simone -> CODER VP routing)
- `tests/...` new unit + integration suites for VP runtime lifecycle

(Exact module split may be adjusted during implementation, but all deviations must be logged in Program Control Center.)
