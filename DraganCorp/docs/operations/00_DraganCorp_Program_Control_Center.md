# 00. DraganCorp Program Control Center (Source of Truth)

This is the canonical control document for DraganCorp execution status, scope changes, decisions, and lessons learned across all phases.

## 1) How to use this document

1. **Single source of truth rule:** if status/scope changed, update this file first.
2. **Plan vs execution split:**
   - detailed implementation blueprint lives in `02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md`
   - this file tracks actual progress, decisions, and drift.
3. **Change control rule:** no architectural or scope change is considered accepted until recorded in Section 6.
4. **Session cadence:** update Sections 4-9 at end of each implementation session.

---

## 2) Canonical document map

| Purpose | Canonical file |
|---|---|
| Program status + governance | `docs/operations/00_DraganCorp_Program_Control_Center.md` |
| Phase A implementation blueprint | `docs/operations/02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md` |
| Architecture baseline | `docs/architecture/01_Multi_Primary_Agent_Governance_And_Factory_Architecture_2026-02-15.md` |
| Deployment topology decision | `docs/decisions/ADR-001-control-plane-and-deployment-topology.md` |
| Mission protocol spec | `specs/mission-envelope-v1.md` |
| Agent/skill overlay spec | `specs/agent-skill-overlay-contract-v1.md` |

---

## 3) Program phase board

| Phase | Goal | Status | Owner | Exit criteria |
|---|---|---|---|---|
| Phase A | Persistent CODER VP session (no factories) | IN_PROGRESS | DraganCorp Core | CODER VP can be dispatched/resumed/recovered with mission ledger + fallback path |
| Phase B | Shared-VPS multi-runtime VP lanes | PLANNED | DraganCorp Core | Separate VP runtime service operational with quotas + callbacks |
| Phase C | Clone-ready template package | PLANNED | DraganCorp Core | Reusable clone profile + deployment checklist validated |
| Phase D | First autonomous factory pilot | PLANNED | DraganCorp Core | Pilot SLOs met with acceptable maintenance overhead |

---

## 4) Current sprint focus (Phase A)

### 4.1 Sprint objective

Deliver a production-safe CODER VP session lane with persistent identity/session/workspace continuity, while preserving existing `code-writer` fallback and no regression to current Simone orchestration paths.

### 4.2 Active checklist

- [x] A0: finalize Phase A acceptance contract
- [x] A1: implement VP session registry schema + data API
- [ ] A2: implement CODER VP mission dispatch contract
- [ ] A3: integrate Simone routing with guarded rollout flag
- [ ] A4: implement observability fields + dashboards/log queries
- [ ] A5: add fallback/recovery + test suite
- [ ] A6: run controlled rollout and capture lessons

---

## 5) Live status log

| Date (UTC) | Area | Update | Impact | Next action |
|---|---|---|---|---|
| 2026-02-15 | Architecture | Updated to persistent VP-first phased model (A -> B -> clones) | Clarifies immediate implementation sequence | Execute Phase A implementation blueprint |
| 2026-02-15 | Documentation | DraganCorp scaffold + architecture memo + ADR + baseline specs created | Foundation established | Add detailed Phase A implementation documents |
| 2026-02-15 | Phase A Implementation | Added VP session registry spec and mission-envelope VP linkage updates; implemented durable VP registry state APIs + tests | Completes A0/A1 baseline for persistence contract and data layer | Start A2 CODER VP mission dispatch runtime adapter |

---

## 6) Change control register

| Change ID | Date (UTC) | Type (scope/architecture/process) | Description | Requested by | Decision | Affected docs |
|---|---|---|---|---|---|---|
| CR-001 | 2026-02-15 | architecture | Re-prioritize to persistent CODER VP session before factories | User | ACCEPTED | Architecture 01, ADR-001 |
| CR-002 | 2026-02-15 | architecture | Add shared-VPS multi-runtime phase before clone factories | User | ACCEPTED | Architecture 01, ADR-001 |

---

## 7) Decision log (implementation-level)

| Decision ID | Date (UTC) | Decision | Rationale | Revisit trigger |
|---|---|---|---|---|
| D-IMP-001 | 2026-02-15 | Use gateway-managed session lifecycle + VP session registry as persistence anchor | Avoid CLI `--resume` ambiguity; improve deterministic continuity | If gateway persistence model changes materially |
| D-IMP-002 | 2026-02-15 | Preserve `code-writer` path as fallback during Phase A | Zero-regression rollout requirement | If fallback usage remains high beyond stabilization window |
| D-IMP-003 | 2026-02-15 | Implement VP registry as durable DB-first contract before runtime adapter wiring | Enables deterministic resume/recovery semantics and testable lifecycle operations | If runtime adapter requires additional registry fields beyond current schema |

---

## 8) Risk and mitigation ledger

| Risk ID | Risk | Probability | Impact | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|
| R-001 | Session orphaning/drift for CODER VP | Medium | High | Lease/heartbeat + recovery flow + stale-session sweeper | Core | OPEN |
| R-002 | Routing regressions from Simone to CODER VP | Medium | High | Feature flag + shadow mode + fallback to `code-writer` | Core | OPEN |
| R-003 | Shared subscription contention | Medium | Medium | Lane quotas + backpressure policy | Core | OPEN |
| R-004 | Added complexity without measurable gain | Low | Medium | Phase gates with explicit success criteria | Core | OPEN |

---

## 9) Lessons learned (rolling)

| Date (UTC) | Lesson | Action taken |
|---|---|---|
| 2026-02-15 | Architecture needed explicit distinction between VP runtime lanes and clone factories | Added phased model and promotion criteria in architecture + ADR |

---

## 10) Update protocol (mandatory)

At the end of each development session:

1. Update Section 4 checklist progress.
2. Append one row in Section 5 status log.
3. Record any accepted scope/design change in Section 6.
4. Record implementation-level design choices in Section 7.
5. Add new risks/mitigations in Section 8.
6. Capture one learning in Section 9 when relevant.

If an item is not in this file, treat it as **not yet accepted**.
