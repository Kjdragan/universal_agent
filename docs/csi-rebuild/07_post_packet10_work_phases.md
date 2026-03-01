# CSI Post-Packet-10 Work Phases (AI Handoff Plan)

Last updated: 2026-03-01 17:28 America/Chicago  
Owner: Codex  
Audience: next AI coder taking over CSI rebuild execution

## Read This First
1. [00_master_plan.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/00_master_plan.md)
2. [06_packet_handoff.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/06_packet_handoff.md)
3. [status.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/status.md)
4. [05_incident_log.md](/home/kjdragan/lrepos/universal_agent/docs/csi-rebuild/05_incident_log.md)

## Current Baseline
- Packets `1-13`: complete in source (packet 11-13 now implemented with tests and docs updates).
- Current objective: packet `14` artifact discoverability + traceability flow (notification -> session -> artifact).
- Reliability autopilot baseline is active: canary alerts, guarded remediation, and daily SLO gatekeeper are all wired.

## Operating Contract For Any New AI Coder
1. Execute exactly one packet at a time unless explicitly marked parallel-safe.
2. For each packet: code -> tests -> docs update -> commit -> deploy -> verify.
3. Update these docs on every packet close:
   - `docs/csi-rebuild/status.md`
   - `docs/csi-rebuild/05_incident_log.md` (only if there was a defect/regression)
4. Never mix unrelated infrastructure work into CSI packet commits.
5. Keep packet commits small and reversible.

## Remaining Work Phases (After Packet 10)
| Phase | Goal | Packet Range | Exit Gate |
|---|---|---|---|
| Phase 2 | Reliability Autopilot | 11-13 | Regressions auto-detected, auto-alerted, and safely auto-remediated for known failure classes. |
| Phase 3 | Research Quality and Artifact UX | 14-16 | CSI outputs are discoverable, rankable, and useful to operators without manual DB/log digging. |
| Phase 4 | Closed-Loop UA<->CSI Orchestration | 17-19 | UA can request/refine CSI research loops deterministically with budget and evidence controls. |
| Phase 5 | Scale, Governance, and Release Readiness | 20-22 | CSI meets SLO/SLA gates, runbooks are complete, and operations are handoff-ready. |

## Packet Backlog (Execution-Ready)

## Packet 11 (Complete): Canary-Aware Operator Panel
- Objective: make packet 10 canary signals first-class in dashboard and notifications.
- Deliverables:
  - `delivery_health_regression` and `delivery_health_recovered` surfaced as dedicated operator cards.
  - “Run remediation” command buttons (copy + execute path via existing workflow conventions).
  - Alert dedupe/cooldown UX so panel noise stays low.
- Validation:
  - Gateway tests for severity/requires_action mapping.
  - UI tests/smoke for canary card rendering.
- Exit criteria:
  - Operator can diagnose a regression from UI alone in under 60 seconds.

## Packet 12 (Complete): Guarded Auto-Remediation Runner
- Objective: automate safe remediations for known faults.
- Deliverables:
  - Automated playbooks for:
    - DLQ replay when endpoint/auth healthy.
    - adapter stall recovery sequence.
    - digest cursor reset when stale cursor detected.
  - Hard safety checks (max attempts, cooldown, no infinite loops).
  - Remediation audit trail in notifications metadata.
- Validation:
  - Unit tests for guardrails/cooldowns.
  - Integration test simulating stale/failed source then recovery.
- Exit criteria:
  - System can self-heal common failures without human action and without flapping.

## Packet 13 (Complete): Reliability SLO Gatekeeper
- Objective: enforce reliability budgets automatically.
- Deliverables:
  - Daily SLO computation job for:
    - delivery success ratio,
    - DLQ backlog trend,
    - source freshness lag,
    - canary regression frequency.
  - “SLO breached” event with top 3 root-cause candidates.
- Validation:
  - Backfilled historical computation test.
  - Runtime timer/service verification.
- Exit criteria:
  - Clear pass/fail daily reliability status with explainable metrics.

## Packet 14: Artifact Discoverability and Traceability
- Objective: eliminate “report exists but I can’t find it” operator pain.
- Deliverables:
  - Normalize artifact registry fields in activity metadata (`report_key`, artifact paths, source).
  - Add explicit “Open report artifact” links/actions from CSI/Notifications/Sessions views.
  - Consistent mapping from notification -> session -> artifact.
- Validation:
  - API tests for artifact metadata completeness.
  - Manual flow: alert -> open report -> open session.
- Exit criteria:
  - Every high-value CSI report is clickable from the notification detail panel.

## Packet 15: Session Rehydrate Reliability (CSI handoff sessions)
- Objective: fix read-only attached session rehydrate gaps.
- Deliverables:
  - Ensure attached tail sessions can rehydrate context summary in chat pane.
  - When full run history absent, show structured reason and next action (not blank panel).
  - Add API diagnostics for session memory mode and run-log linkage.
- Validation:
  - Session API tests for attached/readonly/empty-history variants.
  - UI smoke for “attach to chat” and rehydrate path.
- Exit criteria:
  - No “No run history found” dead-end without explanation + actionable fallback.

## Packet 16: Research Quality Scoring v1
- Objective: quantify report usefulness beyond volume metrics.
- Deliverables:
  - Per-report quality score composed of:
    - evidence coverage,
    - novelty,
    - source diversity,
    - actionability.
  - Quality score included in `report_product_ready` metadata and UI.
- Validation:
  - Unit tests for scoring function.
  - Regression test with fixed fixtures.
- Exit criteria:
  - Operators can sort and filter reports by quality score.

## Packet 17: UA-Driven Follow-Up Contract v2
- Objective: make UA<->CSI follow-up deterministic.
- Deliverables:
  - Explicit request/response schema for refinement loops.
  - Correlation IDs for all follow-up events.
  - Hard budget and timeout policy in metadata.
- Validation:
  - Gateway ingest contract tests.
  - End-to-end test from UA request to CSI follow-up completion event.
- Exit criteria:
  - Every follow-up request has a traceable outcome or timeout artifact.

## Packet 18: Iterative Refinement Policy Engine
- Objective: stop “activity that means nothing” by enforcing quality thresholds.
- Deliverables:
  - Policy decides whether to:
    - close loop,
    - request targeted follow-up,
    - escalate with repair guidance.
  - Confidence + quality + freshness combined in one decision record.
- Validation:
  - Policy table tests for each branch outcome.
  - Simulation run with mixed-source signal sets.
- Exit criteria:
  - Follow-up loops are bounded and show measurable report improvement.

## Packet 19: High-Signal Publishing Pipeline
- Objective: publish only meaningful outputs to Telegram/UA channels.
- Deliverables:
  - Filtered publish tiers:
    - critical regressions,
    - high-confidence opportunities,
    - daily executive digest.
  - Suppress repetitive low-value status messages.
- Validation:
  - Notification feed tests for suppression/dedupe.
  - Telegram digest dry-run output checks.
- Exit criteria:
  - Feeds show fewer but higher-value updates with clear action context.

## Packet 20: Source Coverage Expansion Controls
- Objective: scale sources without destabilizing pipeline.
- Deliverables:
  - Coverage controls for large watchlists (RSS/Reddit sharding and per-source quotas).
  - Feature-flag scaffold for next source (for example X/Threads).
  - Backpressure behavior documented and tested.
- Validation:
  - Load tests for large watchlist polling windows.
  - Source starvation prevention checks.
- Exit criteria:
  - Higher source count without ingestion starvation or alert storming.

## Packet 21: Operations Governance Pack
- Objective: production-grade ops handoff completeness.
- Deliverables:
  - Final runbooks:
    - incident triage,
    - remediation escalation,
    - rollback,
    - data repair.
  - On-call quick commands and expected outputs.
  - “What good looks like” dashboard screenshots/criteria.
- Validation:
  - Fresh-operator drill: recover from seeded regression using runbooks only.
- Exit criteria:
  - New operator can resolve common failures without tribal knowledge.

## Packet 22: Release Candidate Soak + GA Gate
- Objective: certify CSI rebuild for stable operations.
- Deliverables:
  - 72h soak validation report.
  - SLO compliance summary.
  - Open-risk list with explicit owner and due date.
- Validation:
  - Live canary and delivery-health trend data over soak window.
  - No unresolved P1/P2 regressions.
- Exit criteria:
  - GA sign-off for CSI v2 reliability + usefulness baseline.

## Non-Negotiable Quality Gates Per Packet
- Tests:
  - relevant unit/integration tests pass,
  - gateway tests pass for changed contracts,
  - web build passes for UI changes.
- Deploy:
  - packet deployed to VPS,
  - service/timer active if applicable,
  - live verification completed.
- Documentation:
  - update `status.md` with packet state,
  - append validation evidence,
  - record incident if any regression occurred.

## Suggested Commit Format
- `feat(csi): packet <N> <short-description>`
- `fix(csi): packet <N> <short-description>`
- `ops(csi): packet <N> <short-description>`
- `docs(csi): packet <N> <short-description>`

## Handoff Prompt Snippet (Copy/Paste For New AI Coder)
Use this exactly as kickoff context:

```text
You are taking over CSI rebuild execution at packet 10+.
Read docs/csi-rebuild/06_packet_handoff.md and docs/csi-rebuild/07_post_packet10_work_phases.md first.
Execute one packet at a time with full validation and deployment.
After each packet:
1) update docs/csi-rebuild/status.md
2) add validation evidence
3) record incidents in docs/csi-rebuild/05_incident_log.md when applicable
Do not mix unrelated changes into packet commits.
```
