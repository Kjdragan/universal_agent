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
| Phase A VP observability playbook | `docs/operations/03_Phase_A_CODER_VP_Observability_Playbook.md` |
| Phase A rollout evidence log | `docs/operations/04_Phase_A_Controlled_Rollout_Evidence_Log.md` |
| Phase A execution operating model | `docs/operations/05_Phase_A_Execution_Operating_Model_and_Meta_Prompt.md` |
| Phase A sustained default-on model | `docs/operations/07_Phase_A_Sustained_Default_On_Operating_Model_and_Meta_Prompt.md` |

---

## 3) Program phase board

| Phase | Goal | Status | Owner | Exit criteria |
|---|---|---|---|---|
| Phase A | Persistent CODIE session (no factories) | IN_PROGRESS | DraganCorp Core | CODIE can be dispatched/resumed/recovered with mission ledger + fallback path |
| Phase B | Shared-VPS multi-runtime VP lanes | PLANNED | DraganCorp Core | Separate VP runtime service operational with quotas + callbacks |
| Phase C | Clone-ready template package | PLANNED | DraganCorp Core | Reusable clone profile + deployment checklist validated |
| Phase D | First autonomous factory pilot | PLANNED | DraganCorp Core | Pilot SLOs met with acceptable maintenance overhead |

---

## 4) Current sprint focus (Phase A)

### 4.1 Sprint objective

Deliver a production-safe CODIE (CODER VP) session lane with persistent identity/session/workspace continuity, while preserving existing `code-writer` fallback and no regression to current Simone orchestration paths.

### 4.2 Active checklist

- [x] A0: finalize Phase A acceptance contract
- [x] A1: implement VP session registry schema + data API
- [x] A2: implement CODER VP mission dispatch contract
- [x] A3: integrate Simone routing with guarded rollout flag
- [x] A4: implement observability fields + dashboards/log queries
- [x] A5: add fallback/recovery + test suite (includes bootstrap/exception + restart/lease-recovery/stale-session coverage)
- [x] A6: run controlled rollout and capture lessons (completed: limited cohort + broadened windows + first sustained default-on monitoring cycle captured)

### 4.3 Acceptance evidence snapshots (A2/A3 close)

| Workstream | Evidence | Notes |
|---|---|---|
| A2 mission dispatch contract | `src/universal_agent/vp/coder_runtime.py`, `tests/durable/test_coder_vp_runtime.py` | Mission lifecycle persisted via `vp_missions` and `vp_events` with dispatch/progress/completion/failed/fallback events |
| A3 guarded routing | `src/universal_agent/gateway.py`, `tests/api/test_gateway_coder_vp_routing.py` | Flag-gated routing decision, CODER VP delegation, and deterministic fallback path verified across happy/error/bootstrap/exception/restart scenarios |

---

## 5) Live status log

| Date (UTC) | Area | Update | Impact | Next action |
|---|---|---|---|---|
| 2026-02-15 | Architecture | Updated to persistent VP-first phased model (A -> B -> clones) | Clarifies immediate implementation sequence | Execute Phase A implementation blueprint |
| 2026-02-15 | Documentation | DraganCorp scaffold + architecture memo + ADR + baseline specs created | Foundation established | Add detailed Phase A implementation documents |
| 2026-02-15 | Phase A Implementation | Added VP session registry spec and mission-envelope VP linkage updates; implemented durable VP registry state APIs + tests | Completes A0/A1 baseline for persistence contract and data layer | Start A2 CODER VP mission dispatch runtime adapter |
| 2026-02-15 | Phase A Implementation | Implemented gateway-level CODER VP runtime adapter baseline: flag-driven routing decision, VP lane session bootstrapping, durable mission/event lifecycle wiring, and automatic fallback path | Establishes WS-C core dispatch contract and WS-D guarded routing foundation without breaking primary path | Validate gateway integration behavior and extend WS-F integration tests |
| 2026-02-15 | Phase A Implementation | Hardened CODER VP fallback semantics in gateway: bootstrap failures and delegated hard exceptions now deterministically fall back to primary path; expanded integration coverage for bootstrap failure + exception fallback payloads | Reduces regression risk for A5 recovery/fallback and improves observability of fallback causes in mission events | Continue WS-E observability fields and operator-facing queries |
| 2026-02-15 | Phase A Observability | Added `/api/v1/ops/metrics/coder-vp` with runtime DB-backed VP session/mission/event aggregation (fallback rate + latency stats + parsed event payloads) and endpoint tests | Delivers first operator-facing WS-E query surface for CODER VP rollout safety checks | Add dashboard/log query playbook and session recovery drill guidance |
| 2026-02-16 | Phase A Observability | Added dashboard-facing CODER VP metrics route (`/api/v1/dashboard/metrics/coder-vp`), created Phase A observability playbook with curl/query interpretation guidance, and fixed two failing ops API tests by hardening calendar missed-event resolution visibility and stabilizing session-policy test auth profile setup | Restores broad suite stability and provides an operator-ready observability workflow for guarded rollout decisions | Continue WS-E dashboard consumption wiring and rollout evidence capture |
| 2026-02-16 | Phase A Completion Push | Added CODER VP rollout widget to dashboard shell, enabled right-side vertical scroll on main dashboard, extended recovery coverage with restart + lease-recovery + stale-session tests, and started controlled rollout evidence log | Closes A2/A3 formally, completes A4/A5 implementation scope, and starts A6 execution discipline | Run shadow observation window and append first live metrics evidence row |
| 2026-02-16 | Phase A Controlled Rollout | Captured first baseline shadow snapshot for `vp.coder.primary` (`fallback.rate=0.0`, `missions_considered=0`, `p95_latency=null`) and logged evidence artifact | Starts A6 with objective baseline evidence before traffic promotion decisions | Execute first full shadow window and append comparative row with mission volume |
| 2026-02-16 | Phase A Controlled Rollout | Executed traffic-bearing shadow simulation window (4 CODER VP missions with one injected VP exception) and captured metrics snapshot (`fallback.rate=0.25`, `p95_latency=0.534s`) in evidence log | Validates observability signal path under mixed success/fallback conditions; confirms decision discipline before promotion | Run next shadow window without fault injection and compare fallback/latency against promotion gates |
| 2026-02-16 | Phase A Controlled Rollout | Executed clean traffic-bearing shadow simulation window (4 CODER VP missions, no injected failures) and captured metrics snapshot (`fallback.rate=0.0`, `p95_latency=0.377s`) | Demonstrates gate-compatible fallback/latency behavior in simulation and supports transition to limited cohort pilot | Run limited real cohort window and append first non-synthetic evidence row |
| 2026-02-16 | Phase A Controlled Rollout | Added `scripts/coder_vp_rollout_capture.py` and playbook automation instructions to generate structured snapshot + markdown evidence rows in direct or HTTP mode | Improves repeatability and reduces manual error risk for A6 evidence capture across future windows | Use helper for first limited real cohort evidence row |
| 2026-02-16 | Phase A Controlled Rollout | Validated HTTP-mode capture path and received `401 Unauthorized` when querying local gateway without ops token; hardened helper error guidance to surface token requirement | Confirms non-synthetic limited-cohort capture path is wired but auth-gated in current shell environment | Set `UA_OPS_TOKEN` (or pass `--ops-token`) and run first real cohort capture |
| 2026-02-16 | Phase A Controlled Rollout | Investigated execution stalls and found two rollout blockers: gateway was running with CODER VP disabled (`UA_ENABLE_CODER_VP=0`) and stale session runs could remain `active_runs=1` after cancellation | Identified root cause of repeated `missions_considered=0` despite cohort probes and explained timeout behavior from WS clients | Restart gateway with CODER VP enabled and harden cancellation path for stale task cleanup |
| 2026-02-16 | Phase A Controlled Rollout | Added gateway session execution-task tracking + cancellation hardening (`TURN_STATUS_CANCELLED`, per-session task registry, cancel-time task abort/fallback turn finalization), then verified first non-synthetic limited cohort mission (`fallback=0`, `missions_considered=1`) | Converts A6 from blocked to measurable real traffic with deterministic operator controls for stuck runs | Capture at least one additional real cohort window before final promotion decision |
| 2026-02-16 | Phase A Controlled Rollout | Ran second limited cohort window: first probe timed out but cleanup path reconciled session to `active_runs=0`; follow-up probe completed and metrics advanced to `missions_considered=2` with zero fallback | Confirms cancellation fallback cleanup works operationally and rollout health remains within gate thresholds on real traffic | Capture one more real window and decide whether to expand beyond limited cohort |
| 2026-02-16 | Phase A Controlled Rollout | Completed third limited real cohort window (`missions_considered=3`, `missions_with_fallback=0`, `fallback.rate=0.0`) after successful WS delegated execution and HTTP capture | Establishes a three-mission real cohort sample with no fallback events and stable p95 at observed range, strengthening promotion readiness evidence | Review promotion gates and decide on broadened traffic enablement |
| 2026-02-16 | Phase A Controlled Rollout | Completed fourth limited real cohort window (`missions_considered=4`, `missions_with_fallback=0`, `fallback.rate=0.0`) and documented conditional broadened-traffic recommendation with guardrails | Extends real-traffic evidence set while preserving explicit rollback criteria and monitoring controls for safe rollout expansion | Execute broadened rollout with guardrails and continue periodic evidence capture |
| 2026-02-16 | Phase A Controlled Rollout | Executed first broadened rollout window under guardrails; CODER VP metrics advanced to `missions_considered=6` with `missions_with_fallback=0` and no VP failures observed | Confirms broadened rollout can progress while preserving fallback safety envelope; identifies intent-routing coverage as a monitoring dimension (one probe remained on primary path) | Continue broadened windows and track both fallback rate and delegated-routing share |
| 2026-02-16 | Phase A Controlled Rollout | Executed second broadened rollout window (three additional real coding prompts) and advanced CODER VP metrics to `missions_considered=9` with `missions_with_fallback=0` | Reinforces broadened rollout stability with larger real sample and no VP failure/fallback events; routing coverage improved to full delegation in this window | Continue broadened monitoring cadence and evaluate readiness for sustained default-on posture |
| 2026-02-16 | Phase A Controlled Rollout | Executed third broadened rollout window (three additional real coding prompts) and advanced CODER VP metrics to `missions_considered=12` with `missions_with_fallback=0` | Confirms broadened rollout remains stable at higher real-traffic volume while preserving zero fallback/failure profile and improved p95 (`35.964s`) | Continue broadened evidence cadence toward sustained default-on recommendation |
| 2026-02-16 | Phase A Controlled Rollout | Executed fourth broadened rollout window (three additional real coding prompts) and advanced CODER VP metrics to `missions_considered=15` with `missions_with_fallback=0` | Strengthens broadened-rollout confidence with larger real sample and unchanged zero fallback/failure profile while p95 remains stable (`35.964s`) | Continue broadened windows until post-promotion observation target is met |
| 2026-02-16 | Phase A Controlled Rollout | Executed fifth broadened rollout window (three additional real coding prompts) and advanced CODER VP metrics to `missions_considered=18` with `missions_with_fallback=0` | Completes post-promotion observation target (next 10+ real missions) while preserving zero fallback/failure profile and stable p95 (`35.964s`) | Prepare sustained default-on operating posture and continue periodic evidence snapshots |
| 2026-02-16 | Phase A Sustained Monitoring | Executed first sustained default-on monitoring cycle (three real coding prompts + low-cost snapshot profile) and advanced CODIE metrics to `missions_considered=20` with `missions_with_fallback=0` | Confirms default-on posture health with zero fallback/failure and stable p95 (`35.964s`) while keeping resource usage modest (`mission_limit=60`, `event_limit=180`) | Continue low-cost sustained cadence (2-4/day steady state; 30-60m during active implementation windows) |

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
| D-IMP-004 | 2026-02-15 | Implement initial CODER VP runtime adapter inside in-process gateway with dedicated VP workspace/session lane and durable mission/event ledger calls | Delivers WS-C dispatch path quickly while keeping rollout guardrails and fallback behavior centralized in gateway routing | If Phase A requires out-of-process VP lane separation before rollout |
| D-IMP-005 | 2026-02-15 | Treat both VP bootstrap errors and delegated runtime exceptions as fallback triggers, not just emitted error events | Ensures fallback contract remains deterministic even when delegated adapter fails before/while streaming events | If out-of-process runtime supervision introduces richer failure classification |
| D-IMP-006 | 2026-02-15 | Expose CODER VP rollout health via a dedicated ops endpoint instead of ad-hoc SQL/manual log inspection | Gives operators a deterministic API for fallback rate/latency/event diagnostics during guarded rollout stages | If Phase B introduces a centralized observability service replacing gateway-level metrics |
| D-IMP-007 | 2026-02-16 | Treat resolved missed cron events (`rescheduled` / `deleted`) as non-displayable in calendar feed while preserving approved backfill visibility | Aligns event feed semantics with operator expectations and existing regression tests; avoids stale missed-event resurfacing | If product direction changes to show historical resolved-missed markers in default feed |
| D-IMP-008 | 2026-02-16 | Promote reclaimed CODER VP sessions from paused/degraded/recovering to active on successful lease acquisition | Makes stale-session takeover deterministic and aligns lease recovery behavior with execution readiness | If paused-state semantics are redesigned to require explicit manual resume |
| D-IMP-009 | 2026-02-16 | Track active WS execution tasks per session and attempt explicit task cancellation during session cancel operations | Prevents stale `active_runs`/turn locks from indefinitely blocking real cohort traffic and improves rollout operator recoverability | If execution runtime adds a canonical cancellation API that supersedes task-level cancellation |
| D-IMP-010 | 2026-02-16 | Approve conditional broadened CODER VP traffic after four real limited-cohort missions with zero fallback | Evidence now shows `missions_considered=4`, `missions_with_fallback=0`, stable p95, and no sustained `vp.mission.failed`; broadening can proceed under explicit rollback guardrails | If fallback exceeds 10% over rolling 20 missions, sustained VP failures appear, or continuity regression alerts fire |
| D-IMP-011 | 2026-02-16 | Activate broadened CODER VP traffic under guardrails and continue scripted evidence capture | Broadened rollout now spans fifteen real missions with fallback still at 0 and no VP failures; phased expansion remains justified with close monitoring | If delegated-routing share materially drops for coding prompts, fallback rate rises above threshold, or VP failure patterns emerge |
| D-IMP-012 | 2026-02-16 | Mark post-promotion observation target complete after broadened sample reached eighteen real missions | Guardrail target (next 10 real missions after broadened activation) is satisfied with `fallback.rate=0.000`, no sustained failures, and stable p95; ready for sustained default-on posture with periodic monitoring | If fallback/failure trend regresses or continuity alerts indicate degradation |
| D-IMP-013 | 2026-02-16 | Shift Phase A CODIE lane to sustained default-on monitoring cadence with lightweight snapshot profile | Post-promotion target and first sustained cycle are healthy; low-cost checks preserve safety signal while avoiding unnecessary load | If sustained snapshots show fallback/failure trend increase or latency degradation beyond watch/critical thresholds |

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
| 2026-02-15 | Fallback logic must account for thrown exceptions in addition to emitted error events from delegated runtimes | Added gateway exception-to-fallback path and integration tests for bootstrap failure and hard runtime exception cases |
| 2026-02-15 | Observability adoption is faster when rollout diagnostics are exposed as stable internal APIs instead of one-off DB inspection | Added `/api/v1/ops/metrics/coder-vp` endpoint and tests to standardize VP lane health checks |
| 2026-02-16 | Test fixture assumptions around deployment profile can cause environment-specific false negatives | Pinned test fixture profile to `local_workstation` for session-policy endpoint tests and validated broad suite pass |
| 2026-02-16 | Dashboard shell layouts can hide critical ops sections when parent containers force `overflow-hidden` | Enabled explicit vertical scrolling in dashboard main content container and added CODER VP rollout card near top-level metrics |
| 2026-02-16 | Fault-injected shadow windows are valuable for validating fallback instrumentation but should not be used for promotion decisions | Logged synthetic-window evidence separately and queued a clean shadow window for promotion-gate evaluation |
| 2026-02-16 | Rollout-gate decisions are easier to keep consistent when evidence-row generation is scripted | Added `scripts/coder_vp_rollout_capture.py` and documented standard usage in the observability playbook |
| 2026-02-16 | Real cohort traffic evidence requires runtime feature-flag parity with rollout intent, not just observability readiness | Restarted gateway with `UA_ENABLE_CODER_VP=1` (and no shadow/force-fallback override) before running limited cohort verification |
| 2026-02-16 | Limited cohort WS probes can still hit occasional long-tail execution latency; operational cancel fallback remains necessary even after task-cancel hardening | Captured timeout-recovery evidence row and validated that cancel fallback resets `active_runs` to keep subsequent real cohort windows unblocked |
| 2026-02-16 | Promotion confidence improves materially when limited cohort evidence includes multiple successful real windows, not just a single pass | Extended real cohort sample to three completed missions with zero fallback before recommending broadened rollout |
| 2026-02-16 | Broadening recommendation quality improves when go/no-go criteria include explicit rollback triggers and observation-window cadence | Added conditional-go recommendation tied to rolling fallback/failure thresholds and continued scripted window capture |
| 2026-02-16 | Broadening analysis should track intent-routing coverage in addition to fallback/latency so coding prompts that miss VP intent are visible early | Recorded broadened-window routing mix (2 delegated, 1 primary) and added delegated-routing share to operational monitoring focus |
| 2026-02-16 | Routing variance can normalize across broadened windows; evaluating trend quality requires multi-window view rather than single-window anomalies | Captured second broadened window with 3/3 delegated routing and kept delegated-routing share as a continuing guardrail metric |
| 2026-02-16 | As broadened traffic volume increases, trend confidence should prioritize rolling fallback/failure outcomes over single-window latency spikes | Extended broadened evidence to twelve real missions with zero fallback/failure while p95 improved to 35.964s |
| 2026-02-16 | Sustained broadened stability should be judged on cumulative mission outcomes; incremental windows mainly confirm trend durability | Extended broadened evidence to fifteen real missions with zero fallback/failure and no routing regressions in latest window |
| 2026-02-16 | Promotion guardrails are easier to operationalize when observation targets are numeric and explicit | Used a concrete post-promotion target (next 10 real missions), reached eighteen real missions with zero fallback, then transitioned recommendation to sustained default-on readiness |
| 2026-02-16 | Sustainable monitoring should optimize for signal quality per query cost, not maximum data pull every cycle | Introduced low-cost sustained profile (`mission_limit=60`, `event_limit=180`) and cadence guidance that stays lightweight while preserving rollback triggers |

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
