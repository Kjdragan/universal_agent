# Scheduling Runtime V2 Event-Driven Architecture Plan
Date: 2026-02-08
Owner: Universal Agent Core
Status: Approved for implementation planning

## 1. Objective
Evolve the scheduling runtime from mixed polling behavior to an event-driven model that:
- Preserves heartbeat as the catch-all operational loop.
- Keeps cron as explicit scheduled job execution.
- Makes calendar a fast, reliable projection of runtime truth.
- Reduces wasted polling work and stale UI state.

## 2. Why V2 Is Needed
V1 is functionally complete but still uses periodic polling in key paths. This creates:
- Unnecessary wakeups when nothing changed.
- Delayed UI freshness.
- Increased complexity in synchronization logic between cron, heartbeat, and calendar.

V2 addresses these issues without discarding V1 behavior.

## 3. Scope and Non-Goals
Scope:
- Runtime/event-flow redesign for cron, heartbeat, and calendar synchronization.
- Push-first state propagation to dashboard surfaces.
- Backfill and missed-event handling aligned with existing approval-driven policy.

Non-goals:
- Replacing heartbeat engine behavior with an unrelated scheduler.
- Replacing cron semantics with a new job system.
- External Google Calendar two-way sync.

## 4. Target Runtime Model
### 4.1 Authoritative Components
- Cron service remains authoritative for cron jobs and one-shot scheduled jobs.
- Heartbeat service remains authoritative for proactive catch-all checks and session-level periodic work.
- Calendar service becomes a projection layer, not an independent scheduler.

### 4.2 Event-Driven Synchronization
- Cron emits structured lifecycle events:
  - `cron_job_created`
  - `cron_job_updated`
  - `cron_job_deleted`
  - `cron_run_started`
  - `cron_run_completed`
  - `cron_run_failed`
- Heartbeat emits lifecycle events:
  - `heartbeat_scheduled`
  - `heartbeat_started`
  - `heartbeat_completed`
  - `heartbeat_failed`
  - `heartbeat_missed`
- Calendar projection subscribes to these events and incrementally updates the read model.

### 4.3 Push-First UI Delivery
- Use server push channel (SSE or websocket stream) for calendar and status updates.
- Use polling only as fallback recovery path.
- Keep manual `Refresh` in UI for operator confidence and troubleshooting.

## 5. Heartbeat, Cron, Calendar Relationship (Definitive)
- Heartbeat is not replaced. It remains the periodic operational catch-all.
- Cron is not replaced. It remains deterministic scheduled execution.
- Calendar does not trigger independent hidden scheduling logic.
- Calendar reflects and controls runtime through explicit action APIs.
- Missed runs enter stasis and require explicit action (`Approve & Run`, `Reschedule`, `Delete`).

## 6. Data and Contracts
### 6.1 Event Envelope
Each emitted event should include:
- `event_type`
- `source` (`cron` or `heartbeat`)
- `source_ref` (job id or session id)
- `owner_id`
- `session_id` (optional)
- `channel` (optional)
- `occurred_at_utc`
- `status`
- `payload`

### 6.2 Calendar Projection Contract
Projection DTO remains compatible with V1 calendar API and extends with:
- `version` (monotonic projection version for client reconciliation)
- `last_event_at_utc`
- `running` (where applicable)

### 6.3 Notification Contract
Failure/missed notifications include:
- Source and reference IDs.
- Direct action links.
- Priority category handling (`low` suppression retained).

## 7. Phased Implementation Plan
### Phase 0: Baseline and Instrumentation
Deliverables:
- Add counters for polling calls, event emissions, and projection lag.
- Capture baseline CPU/wakeup and stale-state metrics.

Tests:
- Metric emission unit tests.
- Smoke test confirming no behavior change.

Exit criteria:
- Baseline metrics available and stable.

### Phase 1: Event Bus and Projection Incremental Updates
Deliverables:
- Internal event bus abstraction for cron/heartbeat lifecycle events.
- Calendar projection updates from events (append/update/delete semantics).

Tests:
- Unit tests for event handling idempotency and ordering tolerance.
- API regression tests for calendar feed parity versus V1 outputs.

Exit criteria:
- Calendar projection correctness equal to V1 with lower recompute cost.

### Phase 2: Push Channel for Calendar and Status
Deliverables:
- SSE or websocket push endpoint for projection deltas.
- Client reconciliation by projection version.

Tests:
- Integration tests for reconnect/replay and out-of-order delivery handling.
- UI tests for real-time updates without manual refresh.

Exit criteria:
- Dashboard state updates from push in normal operation.

### Phase 3: Polling Fallback Reduction
Deliverables:
- Reduce polling intervals and scope to fallback-only code path.
- Retain watchdog polling for degraded mode.

Tests:
- Reliability tests under push-channel disconnect.
- Degraded-mode tests verifying fallback correctness.

Exit criteria:
- Significant reduction in routine polling volume with no correctness loss.

### Phase 4: Scheduler Wake and Missed-Event Hardening
Deliverables:
- Ensure due-work wake logic is driven by scheduler state transitions and explicit wake signals.
- Preserve approval-driven missed-event recovery behavior.

Tests:
- Missed-event stasis action tests (`approve_backfill_run`, `reschedule`, `delete_missed`).
- Cron one-shot and recurrence timing tests.
- Heartbeat run-state/status update tests.

Exit criteria:
- No silent missed runs and consistent stasis queue behavior.

### Phase 5: Rollout, SLO Gate, and Documentation
Deliverables:
- Feature flag rollout plan (`push_enabled`, `event_projection_enabled`).
- Runbook updates and operational troubleshooting notes.
- Final migration notes from V1 to V2.

Tests:
- Full regression suite.
- 24h soak test in development environment.

Exit criteria:
- V2 declared production-ready for default enablement.

## 8. Migration Strategy
- Keep V1 APIs stable while implementing internal V2 plumbing.
- Introduce feature flags with safe defaults.
- Run shadow mode where V1 and V2 projection outputs are compared before cutover.
- Cut over by owner-controlled config after parity criteria are met.

## 9. Success Metrics
V2 is successful when:
- Routine polling volume drops materially from V1 baseline.
- Calendar/status freshness is near-real-time under normal connectivity.
- Missed-event recovery remains operator-controlled and auditable.
- No regression in cron execution reliability or heartbeat catch-all behavior.

## 10. Risks and Mitigations
- Event ordering/race complexity:
  - Mitigation: idempotent handlers, projection versioning, replay-safe updates.
- Push channel instability:
  - Mitigation: automatic reconnect and bounded polling fallback.
- Over-coupling calendar with scheduler internals:
  - Mitigation: strict source-of-truth boundaries and stable API contracts.

## 11. Delivery Definition (V2 "Good Enough")
V2 is complete when:
- Cron + heartbeat + calendar operate as a cohesive event-driven system.
- Polling is fallback-oriented rather than primary.
- Dashboard reflects runtime state quickly and accurately.
- Existing V1 operator controls and safety policies remain intact.

## 12. Phase 0 Execution Log (Implemented)
Date executed: 2026-02-08
Status: Completed

### 12.1 Implemented Changes
- Added scheduling-runtime instrumentation state in gateway runtime.
- Added counter helpers and projection sampling helpers.
- Instrumented calendar/heartbeat endpoints for baseline request counts.
- Instrumented cron and heartbeat runtime event emissions into shared scheduling metrics.
- Added scheduling metrics endpoint:
  - `GET /api/v1/ops/metrics/scheduling-runtime`
- Wired heartbeat service event sink into gateway startup for heartbeat lifecycle metrics.

### 12.2 Baseline Metrics Added
Counters:
- `calendar_events_requests`
- `calendar_action_requests`
- `calendar_change_request_requests`
- `calendar_change_confirm_requests`
- `heartbeat_last_requests`
- `heartbeat_wake_requests`
- `event_emissions_total`
- `cron_events_total`
- `heartbeat_events_total`

Event type buckets:
- `event_counts.cron.<event_type>`
- `event_counts.heartbeat.<event_type>`

Projection samples:
- `builds`
- `duration_ms_last`
- `duration_ms_max`
- `duration_ms_total`
- `duration_ms_avg` (derived)
- `events_total`
- `always_running_total`
- `stasis_total`
- `due_lag_samples`
- `due_lag_seconds_last`
- `due_lag_seconds_max`
- `due_lag_seconds_total`
- `due_lag_seconds_avg` (derived)

Top-level:
- `started_at`
- `uptime_seconds` (derived)

### 12.3 API Contract (Phase 0)
Endpoint:
- `GET /api/v1/ops/metrics/scheduling-runtime`

Response shape:
- `metrics.started_at`
- `metrics.uptime_seconds`
- `metrics.counters`
- `metrics.event_counts`
- `metrics.projection`

### 12.4 Validation Evidence
Executed:
- `uv run pytest -q tests/gateway/test_ops_api.py tests/gateway/test_cron_api.py tests/gateway/test_cron_scheduler.py`
- Result: `27 passed`

Added tests:
- Scheduling runtime metrics endpoint returns counters/projection/event buckets.
- Fixture-level metric-state reset to avoid cross-test contamination.

### 12.5 Notes for Next Phase
- Phase 0 intentionally does not change scheduler behavior.
- Polling remains active; metrics now provide quantitative baseline for Phase 1 and Phase 3 reduction goals.

## 13. Phase 1 Execution Log (Implemented)
Date executed: 2026-02-08
Status: Completed

### 13.1 Implemented Changes
- Added internal scheduling event bus abstraction in gateway runtime:
  - `SchedulingEventBus` with bounded buffer, sequence IDs, subscriber model, and snapshots.
- Added incremental projection state:
  - `SchedulingProjectionState` with idempotent event application and out-of-order tolerance for cron lifecycle events.
- Added feature flag:
  - `UA_SCHED_EVENT_PROJECTION_ENABLED`
  - Default remains off (safe rollout posture).
- Wired cron and heartbeat lifecycle emissions through the event bus.
- Added projection-aware cron calendar read path:
  - When projection flag is enabled, cron calendar feed reads from projection snapshots instead of direct service scans.
  - Fallback path (flag off) retains prior behavior.
- Extended scheduling runtime metrics with event bus and projection state surfaces:
  - `metrics.event_bus`
  - `metrics.projection_state`

### 13.2 Phase 1 Behavioral Guarantees
- No API contract break for existing calendar endpoints.
- Projection path is opt-in via feature flag.
- Event application is idempotent for duplicate cron run events.
- Event ordering tolerance: run records can arrive before job creation without feed corruption.

### 13.3 Validation Evidence
Executed:
- `uv run pytest -q tests/gateway/test_ops_api.py tests/gateway/test_cron_api.py tests/gateway/test_cron_scheduler.py`
  - Result: `29 passed`
- `uv run pytest -q tests/gateway/test_heartbeat_last.py tests/gateway/test_heartbeat_wake.py tests/gateway/test_heartbeat_schedule.py tests/gateway/test_heartbeat_delivery_policy.py tests/gateway/test_heartbeat_mvp.py`
  - Result: `10 passed`

Added tests:
- Projection idempotency and out-of-order handling validation.
- Calendar projection parity check vs non-projection path.

### 13.4 Notes for Phase 2
- Push delivery (SSE/WS delta stream) is still pending.
- Heartbeat projection currently records lifecycle events but calendar heartbeat rendering remains primarily runtime-state derived.
- Phase 2 will build push-first client update path on top of the event bus and projection versioning.

## 14. Phase 2 Execution Log (Implemented)
Date executed: 2026-02-08
Status: Completed

### 14.1 Implemented Changes
- Added scheduling replay endpoint:
  - `GET /api/v1/ops/scheduling/events`
  - Supports `since_seq` and bounded `limit`.
  - Returns event list + projection version metadata for reconciliation.
- Added scheduling SSE stream endpoint:
  - `GET /api/v1/ops/scheduling/stream`
  - Supports replay via `since_seq`.
  - Emits payloads with event sequence and projection version.
  - Includes keepalive heartbeat events when no deltas are available.
  - Added `once=1` mode for deterministic test validation.
- Extended ops auth helper to support token override value for stream use-cases where browser EventSource cannot set custom headers.
- Dashboard wiring:
  - Added scheduling push connection in ops provider.
  - Tracks push state (`status`, `seq`, `projection_version`).
  - Triggers heartbeat/continuity refresh on relevant pushed events with debounce.
  - Calendar section refreshes from push sequence updates.
  - Added fallback polling only when push stream is disconnected.

### 14.2 Reconciliation Contract
Each stream/replay payload now includes:
- Event sequence (`event.seq` or `seq` for heartbeat payloads).
- `projection_version`
- `projection_last_event_seq`

Client behavior:
- Maintains latest seen sequence.
- Uses projection version/sequence to reconcile updates and trigger refresh.
- Falls back to periodic refresh only in disconnected push state.

### 14.3 Validation Evidence
Executed:
- `uv run pytest -q tests/gateway/test_ops_api.py tests/gateway/test_cron_api.py tests/gateway/test_cron_scheduler.py`
  - Result: `30 passed`
- `uv run pytest -q tests/gateway/test_heartbeat_last.py tests/gateway/test_heartbeat_wake.py tests/gateway/test_heartbeat_schedule.py tests/gateway/test_heartbeat_delivery_policy.py tests/gateway/test_heartbeat_mvp.py`
  - Result: `10 passed`
- `npm --prefix web-ui run lint`
  - Result: no errors (existing pre-existing warnings outside this phase remain).

### 14.4 Notes for Phase 3
- Polling is still present for resilience; reduction/removal tuning will be handled in Phase 3.
- Push channel currently drives UI refresh behavior; next phase should tighten polling intervals and gate fallback behavior by health heuristics.

## 15. Phase 3 Execution Log (Implemented)
Date executed: 2026-02-08
Status: Completed

### 15.1 Implemented Changes
- Reduced routine polling and moved status refresh to degraded-only watchdog path:
  - Removed always-on 5s heartbeat polling loop in ops provider.
  - Removed always-on 15s session-continuity polling loop in ops provider.
  - Added degraded-mode watchdog polling only when push status is not connected:
    - heartbeat refresh every 20s (session scoped)
    - continuity refresh every 45s
- Reduced calendar fallback polling cadence from 15s to 30s.
- Added push feature flag enforcement:
  - Backend stream endpoint now honors `UA_SCHED_PUSH_ENABLED` and returns `503` when disabled.
  - Frontend respects `NEXT_PUBLIC_UA_SCHED_PUSH_ENABLED`; push state is marked `Disabled` when off.
- Added stream/replay observability counters:
  - `push_replay_requests`
  - `push_stream_connects`
  - `push_stream_disconnects`
  - `push_stream_event_payloads`
  - `push_stream_keepalives`

### 15.2 Behavioral Outcome
- Normal mode: push stream is primary update mechanism for calendar + heartbeat/continuity refresh triggers.
- Degraded mode: polling continues as bounded watchdog fallback, preserving correctness while reducing baseline polling volume.
- Push can be disabled safely via feature flag without code changes.

### 15.3 Validation Evidence
Executed:
- `uv run pytest -q tests/gateway/test_ops_api.py tests/gateway/test_cron_api.py tests/gateway/test_cron_scheduler.py`
- `uv run pytest -q tests/gateway/test_heartbeat_last.py tests/gateway/test_heartbeat_wake.py tests/gateway/test_heartbeat_schedule.py tests/gateway/test_heartbeat_delivery_policy.py tests/gateway/test_heartbeat_mvp.py`
- `npm --prefix web-ui run lint`

Added tests:
- Stream-disabled feature flag behavior (`503` contract).
- Scheduling runtime metric assertions for replay/connect/disconnect/payload counters.

## 16. Phase 4 Execution Log (Implemented)
Date executed: 2026-02-08
Status: Completed

### 16.1 Implemented Changes
- Hardened cron missed-event reconciliation path:
  - `approve_backfill_run` for cron now executes with the original scheduled timestamp so the resulting run record reconciles to the missed timeline event.
  - Added resolved-missed suppression for timeline rendering:
    - missed entries marked `approved_and_run`, `rescheduled`, or `deleted` are no longer re-surfaced as active missed events.
- Hardened scheduler wake behavior for session-bound cron jobs:
  - Cron jobs with session binding metadata now default to `wake next heartbeat` after completion, even if explicit wake flags are omitted.
  - Explicit wake-disable directives (for example `wake_heartbeat=off`) still suppress wake calls.
  - Existing explicit wake modes (`now` / `next`) remain supported.

### 16.2 Behavioral Outcome
- Missed-event queue actions are now lifecycle-consistent:
  - Approve-and-run resolves the exact missed event.
  - Reschedule/delete removes stale missed reappearance for the same occurrence.
- Due-work wake behavior is more reliable for session-scoped cron runs without requiring extra per-job configuration.
- Heartbeat remains the catch-all loop; cron remains scheduler-of-record.

### 16.3 Validation Evidence
Executed:
- `uv run pytest -q tests/gateway/test_ops_api.py tests/gateway/test_cron_api.py tests/gateway/test_cron_scheduler.py`
  - Result: `33 passed`
- `uv run pytest -q tests/gateway/test_heartbeat_last.py tests/gateway/test_heartbeat_wake.py tests/gateway/test_heartbeat_schedule.py tests/gateway/test_heartbeat_delivery_policy.py tests/gateway/test_heartbeat_mvp.py`
  - Result: `10 passed`
- `npm --prefix web-ui run lint`
  - Result: no errors (existing pre-existing warnings outside this phase remain).

Added tests:
- Cron missed-event stasis action coverage for:
  - `approve_backfill_run`
  - `reschedule`
  - `delete_missed`
- Default-vs-disabled cron heartbeat wake behavior coverage for session-bound cron jobs.

## 17. Phase 5 Execution Log (Implemented)
Date executed: 2026-02-08
Status: Completed (excluding 24h soak window)

### 17.1 Implemented Changes
- Added V2 operational rollout runbook:
  - `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/11_Scheduling_Runtime_V2_Operational_Runbook_2026-02-08.md`
- Added soak automation script:
  - `src/universal_agent/scripts/scheduling_v2_soak.py`
- Added soak launch/status helper scripts:
  - `src/universal_agent/scripts/start_scheduling_v2_soak_24h.sh`
  - `src/universal_agent/scripts/show_scheduling_v2_soak_status.sh`
- Added short-soak execution report:
  - `OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/03_Scheduling_Runtime_V2_Short_Soak_Readiness_2026-02-08.md`
- Added 24h soak in-progress tracker:
  - `OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/04_Scheduling_Runtime_V2_24h_Soak_In_Progress_2026-02-08.md`
- Captured explicit feature-flag rollout sequence:
  - projection enablement
  - push backend enablement
  - push frontend enablement
  - rollback sequence
- Added operational SLO gate checklist for V2 cutover decisions.
- Added troubleshooting entries for:
  - cron availability issues
  - push connection problems
  - missed-event reconciliation checks
  - heartbeat status visibility checks

### 17.2 Exit Criteria Status
- Feature flag rollout plan (`push_enabled`, `event_projection_enabled`): Completed.
- Runbook updates and operational troubleshooting notes: Completed.
- Final migration notes from V1 to V2: Completed in architecture + runbook docs.
- 24h soak test: Pending operator execution window.

### 17.3 Validation Evidence
Executed and still passing after Phase 4/5 completion:
- `uv run pytest -q tests/gateway/test_ops_api.py tests/gateway/test_cron_api.py tests/gateway/test_cron_scheduler.py`
  - Result: `33 passed`
- `uv run pytest -q tests/gateway/test_heartbeat_last.py tests/gateway/test_heartbeat_wake.py tests/gateway/test_heartbeat_schedule.py tests/gateway/test_heartbeat_delivery_policy.py tests/gateway/test_heartbeat_mvp.py`
  - Result: `10 passed`
- `npm --prefix web-ui run lint`
  - Result: no errors (pre-existing warnings outside scheduling scope remain).
- Short automated soak:
  - `duration=60s`, `interval=15s`, total checks `24`, failures `0`.
