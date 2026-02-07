# 003 - Autonomy Dashboard Implementation Plan

## Scope Link

This plan implements `dashboard/Dashboard Documentation/002_AUTONOMY_DASHBOARD_PRD.md`.

## 1) Execution Strategy

1. Build on existing gateway control-plane APIs first.
2. Deliver usable dashboard value in small, secure increments.
3. Introduce autonomy features only after governance and approval controls are live.
4. Operate autonomous-by-default, pausing only at explicit policy checkpoints.

## 2) Milestones

## Milestone A - Security Baseline and Foundations

Target: 1-2 weeks

1. Replace process-wide stdout/stderr redirection with per-session-safe logging approach.
2. Enforce workspace containment for filesystem-sensitive tool operations.
3. Define runtime redaction policy levels and defaults.
4. Add regression tests for cross-session leakage and path-escape attempts.
5. Define deployment profile defaults for local workstation vs standalone node.

Exit Criteria:

1. Multi-session runs no longer cross-contaminate logs/events.
2. Path traversal/escape tests fail closed.
3. Security baseline checklist passes.

## Milestone B - Dashboard Shell V1

Target: 1-2 weeks

1. Implement route-based UI shell with left navigation.
2. Migrate current Chat view into `Chat` tab.
3. Implement tabs backed by existing APIs:
   1. Dashboard
   2. Skills
   3. Cron Jobs
   4. Channels
   5. Settings
4. Add dashboard summary endpoint for aggregate cards.
5. Add notification center panel for completion, approval-needed, and assistance-needed events.

Exit Criteria:

1. Operator can navigate all V1 tabs from one app shell.
2. No feature regressions in existing chat workflow.
3. Basic ops monitoring is visible without terminal access.

## Milestone C - Session Governance

Target: 2-3 weeks

1. Add session policy schema and storage.
2. Add APIs for read/update policy per session.
3. Add governance UI for:
   1. autonomy mode
   2. approvals
   3. limits/budgets
   4. tool profile
4. Integrate approval checks into execution pipeline for risky actions.
5. Implement in-flight checkpoint gates and resume semantics.
6. Add identity mode policy for `persona` vs `operator_proxy` actions.

Exit Criteria:

1. Every session has explicit policy state.
2. High-risk actions are blocked or routed to approval as configured.

## Milestone D - Mission Queue and Dispatch Engine

Target: 2-3 weeks

1. Implement mission queue persistence and CRUD APIs.
2. Implement dispatcher with priority, aging, and session strategy rules.
3. Add queue UI and operator actions:
   1. approve/reject
   2. reroute
   3. cancel/retry
4. Record dispatch decisions and policy evaluation in audit logs.
5. Emit queue events to notification channels (dashboard + Telegram baseline).
6. Implement global provider concurrency budget manager (default target `3`, tunable `2-4`).
7. Add fairness/aging queue drain policy for saturated provider capacity.

Exit Criteria:

1. Missions can be queued from multiple sources.
2. Dispatcher behavior is deterministic and test-covered.
3. Operator can intervene safely at every stage.

## Milestone E - Goals, To-Do, and Schedule

Target: 2 weeks

1. Implement Goals service and UI.
2. Implement To-Do service and UI.
3. Implement unified schedule feed (cron + missions + heartbeat events).
4. Link goals/todos to mission generation pipelines.
5. Implement schedule mutation flows (reschedule/move/re-prioritize) with conflict checks.
6. Add Google Calendar read/write wiring in Schedule tab via existing Composio-connected capabilities.

Exit Criteria:

1. Operator can drive proactive execution from goals and tasks.
2. Schedule view reflects real runnable workload.

## Milestone F - Hardening and Release

Target: 1-2 weeks

1. Load test parallel sessions and mission throughput.
2. Security validation pass (prompt injection, log leakage, auth checks, approval bypass).
3. Observability dashboards and runbook documentation.
4. Release checklist and rollout plan.
5. Deployment profile hardening: workstation, standalone node, and VPS profiles.
6. Communication escalation architecture, including telephony roadmap (design only).
7. Validate provider rate-limit behavior under load with backoff/queue instrumentation.

Exit Criteria:

1. Production-ready reliability and security thresholds met.
2. Operator documentation complete.

## 3) Work Breakdown by Track

### Track A - Backend APIs

1. `dashboard/summary`
2. `sessions/{id}/policy`
3. `missions/*`
4. `goals/*`
5. `todos/*`
6. `schedule/feed`
7. `missions/{id}/approve|resume`
8. `notifications/*`
9. `schedule/items/{id}`

### Track B - Frontend

1. App shell and routing
2. Shared API client with auth token handling
3. Tab modules and table/timeline components
4. Realtime updates for chat/session state
5. Notification inbox and actionable approval modals
6. Interactive schedule timeline editor

### Track C - Security and Compliance

1. Policy enforcement hooks
2. Redaction middleware/utilities
3. Audit event writer
4. Security regression tests
5. Identity attribution policy checks

### Track D - Platform Reliability

1. Queue worker lifecycle controls
2. Retry/backoff and dead-letter behavior
3. Resource limits and backpressure
4. Deployment profile presets and startup validation
5. Global model concurrency semaphore and scheduler metrics

## 4) Test Plan Gates

Each milestone requires:

1. Unit tests for new domain logic.
2. API integration tests for new endpoints.
3. End-to-end smoke flow in UI.
4. Security regression tests for affected boundaries.

## 5) Delivery Risks and Mitigations

1. Risk: Governance added late, autonomy shipped early.
   1. Mitigation: block autonomous mode release until Milestone C complete.

2. Risk: Multi-session logging leakage persists.
   1. Mitigation: make security baseline (Milestone A) a hard dependency.

3. Risk: Queue complexity causes brittle behavior.
   1. Mitigation: start with deterministic rules and explicit status model.

4. Risk: UI scope balloons.
   1. Mitigation: V1 tabs only on existing APIs, defer net-new domains by milestone.

## 6) Decision Status

### Locked from Interview (2026-02-07)

1. Default identity mode: `persona`; `operator_proxy` explicit override.
2. Hard-stop categories active from day one (payments, non-whitelist email, destructive account/system actions, public posting until toggle).
3. Queue policy: global priority + oldest-ready fairness.
4. Session strategy: auto reuse/spawn with clean-slate preference for unrelated high-priority missions.
5. Notification routing in v1: Telegram + email for approval-required, assistance-required, mission-failed.
6. Approval timeout policy: 111h + one reminder + blocked state.
7. Remote access posture: local auth relaxed, remote token-gated, VPN/Tailscale required for remote web dashboard.
8. Proactive cadence: 30-minute default loop with whitelist-based auto-executable classes.
9. Email whitelist ownership: dashboard-editable with audit history.
10. Calendar mode in v1: full Google Calendar read/write through existing Composio-connected tools.
11. DNA update behavior: apply-on-next-session by default.
12. Telephony scope: design in v1, implementation target in v2.

### Still Open

1. Telephony escalation activation thresholds and trigger policies.
2. Goals scope model: global vs per-session vs hybrid.
