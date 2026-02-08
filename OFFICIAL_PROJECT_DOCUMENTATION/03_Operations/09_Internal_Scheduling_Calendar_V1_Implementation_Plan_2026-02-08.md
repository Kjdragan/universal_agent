# Internal Scheduling Calendar V1 Implementation Plan
Date: 2026-02-08
Owner: Universal Agent Core
Status: Implemented with stabilization updates; V2 follow-on planned

## 1. Objective
Build an internal dashboard calendar for operational scheduling visibility and control, focused on:
- Cron jobs
- Heartbeat/proactive scheduled activity

This calendar is the internal source of operational truth. External calendar sync is out of scope for v1.

## 2. Confirmed Product Decisions
- Scope: cron + heartbeat only.
- Calendar editability: read-only calendar with quick actions.
- Edit path: no form-based edit UI; use natural-language "Request Change".
- Request Change policy: confirm-first.
- Default view: week.
- Mobile behavior: simplified day/list view.
- UI density: compact cards.
- Timezone: store in UTC, display in user timezone (`America/Chicago`).
- Heartbeat color: red.
- Missed runs: visible in calendar.
- Retention window: rolling 30 days.
- Low-priority alert suppression: by category.
- External sync: none in v1, interface stubs only.

## 3. Non-Goals (V1)
- Full in-calendar create/edit forms.
- Google Calendar export/sync.
- Multi-tenant access controls UI (data model remains multi-user ready).
- Arbitrary scheduler replacement for existing cron/heartbeat engines.

## 4. Why Internal Calendar First
- Lower latency than external API round-trips.
- Better consistency with existing runtime state, logs, approvals, and notifications.
- Easier debugging during local development and gateway-mode operations.
- Clear path to future external calendar bridge without changing internal control flow.

## 5. Functional Requirements

### 5.1 Calendar Read Model
Expose a unified projected schedule feed that combines:
- Cron jobs from existing cron service state.
- Heartbeat schedule projections per session.
- Derived run-state overlays (running/succeeded/failed/missed where available).

### 5.2 Views
- Desktop: week grid (default), optional day focus.
- Mobile: day/list only.
- "Always Running" lane for continuous/high-frequency operational checks.

### 5.3 Event Card Model
Each event card should include:
- Title
- Source (`cron` or `heartbeat`)
- Owner
- Session/channel context (if present)
- Scheduled local time
- Status
- Priority category

### 5.4 Quick Actions
V1 action set:
- `Run Now`
- `Pause/Resume` (supported where source supports toggle semantics)
- `Disable`
- `Open Logs`
- `Open Session`
- `Request Change` (natural language, confirm-first)

Implementation note:
- Cron supports full quick-action set via existing cron endpoints plus minor additions if needed.
- Heartbeat may require source-specific action availability; unsupported actions are hidden or disabled with tooltip.

### 5.5 Request Change Flow (Natural Language)
1. User enters NL change request on event card.
2. Backend creates a structured proposed change.
3. UI shows before/after summary.
4. Owner confirms.
5. Backend applies change using cron/heartbeat update path.
6. Audit record created; notification emitted on failure.

## 6. Data and API Design

### 6.1 Event Projection DTO
Proposed contract:
- `event_id`
- `source` (`cron`, `heartbeat`)
- `source_ref` (job_id or heartbeat session key)
- `owner_id`
- `session_id` (optional)
- `channel` (optional)
- `title`
- `description` (optional)
- `category` (`critical`, `normal`, `low`)
- `color_key`
- `status` (`scheduled`, `running`, `success`, `failed`, `missed`, `paused`, `disabled`)
- `scheduled_at_utc`
- `scheduled_at_local`
- `duration_hint_seconds` (optional)
- `timezone_display`
- `actions` (allowed actions list)

### 6.2 New/Updated Endpoints
- `GET /api/v1/ops/calendar/events`
  - Filters: `start`, `end`, `owner`, `source`, `timezone`, `view`.
- `POST /api/v1/ops/calendar/events/{event_id}/action`
  - Actions: `run_now`, `pause`, `resume`, `disable`, `open_logs`, `open_session`.
  - Missed-event actions: `approve_backfill_run`, `reschedule`, `delete_missed`.
- `POST /api/v1/ops/calendar/events/{event_id}/change-request`
  - Body: NL instruction text.
  - Response: proposed patch + confidence + warnings.
- `POST /api/v1/ops/calendar/events/{event_id}/change-request/confirm`
  - Applies previously generated proposal.

### 6.3 Provider Abstraction Stub (Future External Sync)
Introduce interface only:
- `CalendarProvider` with `list_events`, `create_event`, `update_event`, `delete_event`.
- V1 implementation: `InternalCalendarProvider`.
- Placeholder registration for future `GoogleCalendarProvider`.

## 7. Timezone and Scheduling Semantics
- Persist canonical timestamps in UTC.
- Render all calendar UI in user timezone (`America/Chicago`).
- Recurrence expansion occurs in canonical time model, then rendered in local time.
- DST handling delegated to timezone-aware conversion.

## 8. Missed Runs and Backfill Policy
- Calendar always displays missed runs.
- No automatic immediate rerun on service recovery.
- Existing subsystem behavior (cron/heartbeat current behavior) remains authoritative.
- Missed events enter `stasis` and generate notification-center items.
- Notification action buttons for missed events:
  - `Approve & Run`
  - `Reschedule`
  - `Delete`
- Backfill execution is confirm/approval-driven, not automatic.
- Manual recovery path remains available via `Run Now` from calendar cards.

## 9. Notifications and Priority Categories
- Failures generate notification-center alerts by default.
- Category `low` may suppress failure alerts by policy.
- Suppression policy is category-based (not per-event toggle in v1).
- Notification payload includes event source, owner, and direct link targets.
- Missed-event notifications include backfill actions (`Approve & Run`, `Reschedule`, `Delete`).

## 10. Permissions and Ownership
- Current runtime: single owner with admin control.
- Model supports owner-scoped data for future multi-user adoption.
- Guest users are out of scope for v1 UI policy screens, but ownership fields are mandatory in event DTO.

## 11. UX Blueprint
- New dashboard tab: `Calendar`.
- Header controls:
  - Week/Day selector
  - Today jump
  - Refresh
  - Source filter (`all`, `cron`, `heartbeat`)
- Legend:
  - Red = heartbeat
  - Distinct colors for cron categories/status.
- Card interactions:
  - Single-click opens compact details and quick actions.
  - `Request Change` opens inline prompt + proposal confirmation panel.

## 12. Phased Implementation Plan

### Phase 1: Backend Calendar Projection (Read-Only)
Deliverables:
- Unified event projection service.
- `GET /api/v1/ops/calendar/events`.
- UTC/local time rendering fields.
- 30-day rolling retention query window.

Tests:
- Unit tests for recurrence expansion and timezone conversion.
- API tests for filtering, ordering, and retention cutoffs.
- Regression tests to ensure existing cron/heartbeat endpoints unaffected.

Exit criteria:
- Calendar feed stable and accurate for cron + heartbeat across 7-day and 30-day windows.

### Phase 2: Dashboard Calendar UI (Read + Visual Status)
Deliverables:
- New `Calendar` tab in web UI.
- Desktop week grid and mobile day/list view.
- Source filters, legend, and status badges.
- "Always Running" lane.

Tests:
- Frontend unit tests for rendering/grouping.
- Mobile viewport tests.
- Manual E2E verification for week/day toggles and timezone display.

Exit criteria:
- Users can visually inspect scheduled activity without using Cron tab.

### Phase 3: Quick Actions
Deliverables:
- Action wiring for `Run Now`, `Pause/Resume`, `Disable`, `Open Logs`, `Open Session`.
- Source-aware action support (hide unsupported actions cleanly).
- Action result toasts and state refresh.

Tests:
- API contract tests for each action.
- UI tests for action success/failure states.
- Negative tests for unsupported action on source type.

Exit criteria:
- Operator can control scheduled activity directly from calendar cards.

### Phase 4: Request Change (NL + Confirm-First)
Deliverables:
- `Request Change` entry box on event cards.
- Proposal engine endpoint returning normalized patch.
- Confirm-first apply endpoint.
- Audit logging for request, proposal, confirmation, apply outcome.

Tests:
- Unit tests for proposal parsing/validation paths.
- API tests for confirm-first enforcement.
- UI tests for proposal preview and confirmation flow.

Exit criteria:
- Operators can safely request schedule changes in plain language without direct form editing.

### Phase 5: Alerts, Hardening, and Documentation
Deliverables:
- Category-based low-priority alert suppression.
- Notification-center integration for scheduling failures.
- Missed-event stasis queue and action-driven backfill workflow.
- Operator docs and runbook updates.
- Future provider stub wiring (no external sync behavior enabled).

Tests:
- Alert routing tests by category and failure mode.
- Long-run reliability tests (scheduler + UI refresh loops).
- Full regression suite across ops dashboard, cron, heartbeat, and session continuity.

Exit criteria:
- v1 considered operationally reliable with documented runbook.

## 13. Testing Strategy Summary
- Unit:
  - Projection logic
  - Timezone conversions
  - Status derivation
- API integration:
  - Calendar feed
  - Actions
  - Change-request lifecycle
- Frontend:
  - Week/day rendering
  - Mobile list behavior
  - Action and confirmation UX
- E2E/manual:
  - Start stack, schedule jobs, observe, trigger failures, recover with quick actions.

## 14. Risks and Mitigations
- DST/timezone edge cases:
  - Mitigation: UTC canonical storage + tested timezone render layer.
- Heartbeat action mismatch with cron semantics:
  - Mitigation: source-aware action matrix, clear unsupported action handling.
- Proposal ambiguity in NL change requests:
  - Mitigation: confirm-first required, explicit before/after preview.
- Dashboard complexity growth:
  - Mitigation: compact cards and detail offload to Cron tab.

## 15. Delivery Definition (V1 "Good Enough")
V1 is complete when:
- Calendar gives reliable weekly visibility for cron + heartbeat.
- Operators can run/pause/disable/open context from the calendar where supported.
- Missed/failure states are visible and alerting works with category suppression.
- Request Change NL flow functions with confirm-first safety.
- Automated tests and manual validation pass consistently.

## 16. Implementation Closure Snapshot (2026-02-08)
This section records V1 as implemented and distinguishes completed behavior from deferred optimizations.

### 16.1 Implemented in Codebase
- Unified calendar feed endpoint implemented: `GET /api/v1/ops/calendar/events`.
- Calendar action endpoint implemented: `POST /api/v1/ops/calendar/events/{event_id}/action`.
- Missed-event stasis actions implemented: `approve_backfill_run`, `reschedule`, `delete_missed`.
- Request-change proposal + confirm endpoints implemented:
  - `POST /api/v1/ops/calendar/events/{event_id}/change-request`
  - `POST /api/v1/ops/calendar/events/{event_id}/change-request/confirm`
- Dashboard calendar tab implemented with week/day controls and source filter.
- Heartbeat shown in red with source legend and source-aware action handling.
- Cron one-shot lifecycle hardened to avoid pre-consumption before run start.

### 16.2 Stabilization Corrections Applied
- Corrected cron job identity mapping in cron UI (`job_id` usage), preventing stale delete/run targeting.
- Removed unsupported calendar action exposure to avoid runtime unsupported-action failures.
- Added cron running-state surfacing in API/UI and disabled conflicting `Run now` while already running.
- Improved heartbeat status panel refresh behavior tied to active chat session context.

### 16.3 Known Gaps Carried to V2
- Polling-heavy refresh model remains in several UI/status surfaces.
- Scheduler/calendar synchronization is still mixed between polling and event updates.
- Runtime push delivery for calendar/heartbeat state is not yet first-class (SSE/WS-oriented model pending).
- External provider sync remains intentionally stub-only.

## 17. V2 Handoff
V2 is defined in:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/10_Scheduling_Runtime_V2_Event_Driven_Architecture_2026-02-08.md`

V2 scope starts from V1 working behavior and focuses on efficiency and correctness of runtime scheduling/state propagation, not replacement of heartbeat as the operational catch-all.
