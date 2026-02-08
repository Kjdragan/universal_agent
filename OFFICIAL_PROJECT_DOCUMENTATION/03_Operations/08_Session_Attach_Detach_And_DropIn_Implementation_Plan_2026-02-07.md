# 08 Session Attach/Detach and Drop-In Implementation Plan (2026-02-07)

## Purpose

Define and execute a practical session-lifecycle model so Universal Agent supports:

1. Stable background execution independent of UI panel focus.
2. Reliable resume when switching between chat/dashboard panels.
3. Drop-in observation of other sessions from dashboard into chat stream.
4. Safe plurality (desktop + Telegram + API sessions concurrently).

This document is the source plan for implementation sequencing and phase gating.

---

## Problem Statement

Current behavior has session continuity gaps in UI reconnection paths:

1. Session ID persistence in WebSocket connected handling is fragile.
2. Resume/new-session behavior can drift when the client loses the active `session_id`.
3. Dashboard session selection is not yet integrated with chat attach semantics.
4. Full transcript rehydration is not required for immediate value, but tail attach is.

---

## Architectural Position (Locked)

1. `Session` and `Connection` are separate concepts.
2. Session execution lifetime is server-owned; client disconnect is detach only.
3. Multiple sessions across channels are expected and supported.
4. Session memory and long-term memory remain separate concerns.
5. 80/20 strategy: ship reliable attach/resume/tail first, defer complex replay.

---

## Scope

In scope:

1. Session resume hardening.
2. Attach/detach semantics.
3. Dashboard-driven drop-in (tail mode).
4. Multi-client guardrails (single writer, many observers).
5. Idempotency controls for side-effect safety.
6. Testing gates per phase.

Out of scope for initial rollout:

1. Full historical transcript reconstruction in chat UI.
2. Deep retrospective event replay beyond bounded optional replay.
3. Memory ranking model redesign.

---

## Phase Map

1. Phase 1: Session identity/resume hardening.
2. Phase 2: Explicit attach/detach semantics in runtime.
3. Phase 3: Dashboard-to-chat drop-in (tail mode).
4. Phase 4: Writer lock + idempotency keys.
5. Phase 5: Session directory/control plane UX upgrades.
6. Phase 6: Memory policy integration for dev anti-pollution.
7. Phase 7: Observability, SLOs, and rollout gating.

---

## Phase 1: Session Identity/Resume Hardening

### Objectives

1. Ensure client keeps correct `session_id` for resume.
2. Reduce accidental new sessions during panel switches/reconnects.
3. Preserve backward compatibility across event payload variants.

### Implementation Tasks

1. Fix connected-event session extraction in WebSocket client.
2. Accept both nested and top-level `session_id` shapes.
3. URL-encode `session_id` when appending resume query param.
4. Keep current server behavior (resume-if-provided, create-if-missing).

### Code Changes (Executed)

1. `web-ui/lib/websocket.ts`
   - Added robust session ID extraction helper.
   - Persist `session_id` from connected payload (nested or top-level).
   - Encoded querystring `session_id` on connect.
2. `web-ui/lib/store.ts`
   - Hardened connected-event parsing to tolerate both payload shapes.

### Test Gate

Required checks:

1. Frontend lint passes with no new errors.
2. Session resume backend tests pass.
3. API bridge selection tests pass.

### Phase 1 Validation Results (Executed)

1. `npm --prefix web-ui run lint`
   - Passed (repo already has 2 pre-existing warnings in `web-ui/app/page.tsx`, no new errors).
2. `uv run pytest -q tests/gateway/test_gateway.py::TestInProcessGateway::test_resume_existing_session tests/reproduction/test_session_persistence.py::test_session_persistence`
   - Passed (`2 passed`).
3. `uv run pytest -q tests/api/test_agent_bridge_selection.py`
   - Passed (`3 passed`).

### Phase 1 Gate Status

`PASS` (with noted pre-existing lint warnings unrelated to this phase).

---

## Phase 2: Runtime Attach/Detach Semantics

### Objectives

1. Disconnect means detach, not stop.
2. Running session remains active until terminal state.
3. Session metadata reflects runtime reality for re-attach.

### Implementation Tasks

1. Add explicit session lifecycle markers (`running`, `idle`, `terminal`).
2. Ensure disconnect path cannot cancel active execution by default.
3. Record `last_event_seq`, `last_activity_at`, `active_connections`.

### Code Changes (Executed)

1. `src/universal_agent/gateway_server.py`
   - Added runtime session state tracking for:
     - `lifecycle_state`,
     - `last_event_seq`,
     - `last_activity_at`,
     - `active_connections`,
     - `active_runs`.
   - Wired runtime state updates into connection attach/detach and run start/finish.
   - Synced runtime state into `session.metadata["runtime"]`.
   - Updated streaming execution path to broadcast per-session events so attached clients can continue receiving stream events after reconnect.
2. `src/universal_agent/ops_service.py`
   - Session summaries now expose runtime fields and lifecycle-aware status.
3. `src/universal_agent/gateway.py`
   - `list_sessions()` now prefers lifecycle status from runtime metadata when available.
4. `tests/gateway/test_ops_api.py`
   - Added runtime state field test coverage (`running` -> `idle`, counters, event sequence presence).

### Test Gate

1. Disconnect while run is active; run completes without client.
2. Reattach client sees live continuation and terminal status.
3. No orphan/canceled sessions from routine UI navigation.

### Phase 2 Validation Results (Executed)

1. `uv run pytest -q tests/gateway/test_ops_api.py`
   - Passed (`10 passed`).
2. `uv run pytest -q tests/gateway/test_gateway.py tests/api/test_agent_bridge_selection.py`
   - Passed (`17 passed`).
3. `uv run pytest -q tests/gateway/test_ops_api.py tests/gateway/test_gateway.py tests/api/test_agent_bridge_selection.py`
   - Passed (`27 passed` aggregate).

### Phase 2 Gate Status

`PASS`

---

## Phase 3: Dashboard-to-Chat Drop-In (Tail Mode)

### Objectives

1. Allow selecting a session in dashboard and attaching chat to it.
2. Stream from attach point forward (tail) without full replay.

### Implementation Tasks

1. Add dashboard action: `Attach to Chat (Tail)`.
2. Store selected `session_id` in chat connection state and reconnect.
3. Show attached-session banner and mode in chat UI.
4. Optional bounded replay toggle (`last N events`) behind flag.

### Code Changes (Executed)

1. `web-ui/lib/websocket.ts`
   - Added `attachToSession(sessionId)` helper to switch resume target and reconnect.
   - Added explicit stored session helpers for stable resume target updates.
2. `web-ui/components/OpsDropdowns.tsx`
   - Added `Attach To Chat (Tail)` action in Sessions panel.
   - Action resets chat stream state and reconnects WebSocket bound to selected session.
   - Marks chat attach mode as `tail` on attach.
3. `web-ui/lib/store.ts`
   - Added session attach mode state (`default` vs `tail`).
4. `web-ui/app/page.tsx`
   - Added attached-session tail-mode banner in chat UI.
5. `tests/gateway/test_session_dropin.py`
   - Added targeted websocket attach/drop-in tests for Phase 3 gates.

### Test Gate

1. Attach to idle session and receive new events when activity resumes.
2. Attach to active session and receive live stream immediately.
3. Switching attached sessions does not create unsolicited sessions.

### Phase 3 Validation Results (Executed)

1. `uv run pytest -q tests/gateway/test_session_dropin.py`
   - Passed (`3 passed`).
2. `uv run pytest -q tests/gateway/test_session_dropin.py tests/gateway/test_ops_api.py tests/gateway/test_gateway.py tests/api/test_agent_bridge_selection.py`
   - Passed (`30 passed` aggregate).
3. `npm --prefix web-ui run lint`
   - Passed (no new errors; existing unrelated warnings remain in `web-ui/app/page.tsx`).

### Phase 3 Gate Status

`PASS`

---

## Phase 4: Single Writer + Idempotency

### Objectives

1. Prevent conflicting writes when multiple clients target one session.
2. Prevent duplicated side effects on reconnect/retry.

### Implementation Tasks

1. Add per-session turn lock (one active writer).
2. Add `client_turn_id` for each user turn.
3. Deduplicate side-effectful tool invocations by deterministic fingerprint.

### Code Changes (Executed)

1. `src/universal_agent/gateway_server.py`
   - Added per-session turn admission lock and turn-state tracking:
     - `_session_turn_locks`,
     - `_session_turn_state`,
     - `_admit_turn(...)`,
     - `_finalize_turn(...)`.
   - Enforced single-writer behavior:
     - new turns are rejected with `turn_rejected_busy` when another turn is active.
   - Added idempotency handling:
     - explicit `client_turn_id` duplicates return `turn_in_progress` or `duplicate_turn_ignored`,
     - fallback fingerprint dedupe for clients without `client_turn_id`.
   - Included turn metadata in request metadata (`turn_id`, optional `client_turn_id`).
2. `web-ui/lib/websocket.ts`
   - Added `client_turn_id` emission in query payloads for forward-compatible idempotency support.
3. `tests/gateway/test_session_idempotency.py`
   - Added websocket tests for:
     - multi-client writer lock rejection,
     - in-progress duplicate detection,
     - completed duplicate suppression,
     - fallback fingerprint dedupe behavior.

### Test Gate

1. Concurrent submit to same session resolves deterministically.
2. Re-sent same turn does not duplicate side effects.
3. Observers continue streaming while writer lock is held.

### Phase 4 Validation Results (Executed)

1. `uv run pytest -q tests/gateway/test_session_idempotency.py`
   - Passed (`3 passed`).
2. `uv run pytest -q tests/gateway/test_session_idempotency.py tests/gateway/test_session_dropin.py tests/gateway/test_ops_api.py tests/gateway/test_gateway.py tests/api/test_agent_bridge_selection.py`
   - Passed (`33 passed` aggregate).
3. `npm --prefix web-ui run lint`
   - Passed (no new errors; existing unrelated warnings remain in `web-ui/app/page.tsx`).

### Phase 4 Gate Status

`PASS`

---

## Phase 5: Session Directory and Controls

### Objectives

1. Improve operator control over many concurrent sessions.
2. Support attachment/drop-in workflows from ops panel.

### Implementation Tasks

1. Session filters: running/idle/source/owner.
2. Session actions: attach, cancel, archive, reset.
3. Distinguish session source/channel (chat, telegram, api).

### Code Changes (Executed)

1. `src/universal_agent/ops_service.py`
   - Added session source/owner inference in summaries.
   - Added filtering support for `status`, `source`, and `owner`.
   - Added `archive_session(...)` operation for log/state archival without deleting the session folder.
2. `src/universal_agent/gateway_server.py`
   - Extended `GET /api/v1/ops/sessions` to accept `source` and `owner` filters.
   - Added `POST /api/v1/ops/sessions/{session_id}/archive`.
   - Added `POST /api/v1/ops/sessions/{session_id}/cancel`.
   - Unified cancel path through `_cancel_session_execution(...)` (used by WS and ops endpoint).
3. `web-ui/components/OpsDropdowns.tsx`
   - Added session directory filters in UI: status, source, owner.
   - Added session controls: `Cancel Run`, `Archive`, `Attach To Chat (Tail)`, `Reset`, `Delete`.
   - Added source/owner visibility in session cards.
4. `tests/gateway/test_ops_api.py`
   - Added tests for source/owner/status filtering.
   - Added tests for archive and cancel ops actions.

### Test Gate

1. Two independent sessions run in parallel across channels.
2. Attach to either session from dashboard without cross-talk.
3. Controls affect only selected session.

### Phase 5 Validation Results (Executed)

1. `uv run pytest -q tests/gateway/test_ops_api.py`
   - Passed (`12 passed`).
2. `uv run pytest -q tests/gateway/test_session_idempotency.py tests/gateway/test_session_dropin.py tests/gateway/test_gateway.py tests/api/test_agent_bridge_selection.py`
   - Passed (`23 passed` aggregate).
3. `npm --prefix web-ui run lint`
   - Passed (no new errors; existing unrelated warnings remain in `web-ui/app/page.tsx`).

### Phase 5 Gate Status

`PASS`

---

## Phase 6: Memory Policy Integration (Dev Anti-Pollution)

### Objectives

1. Keep development runs from polluting long-term memory.
2. Preserve session memory utility during active testing.

### Implementation Tasks

1. Add memory mode per run/session: `off`, `session_only`, `selective`, `full`.
2. Add tags (`dev_test`, `retain`, `discard`) and retention policy enforcement.
3. Keep dormant Letta path off by default; do not remove integration.

### Code Changes (Executed)

1. `src/universal_agent/session_policy.py`
   - Added normalized session memory policy block:
     - `memory.mode`: `off|session_only|selective|full`
     - `memory.session_memory_enabled`
     - `memory.tags`
     - `memory.long_term_tag_allowlist`
   - Added policy normalization on load/save/update so invalid memory modes collapse to safe defaults.
2. `src/universal_agent/gateway_server.py`
   - Added memory policy snapshot into session metadata.
   - Injected normalized memory policy into request metadata for every turn (including approval-resume path).
   - Extended ops sessions listing endpoint with `memory_mode` filter support.
3. `src/universal_agent/gateway.py`
   - Propagated per-turn `memory_policy` metadata into `ProcessTurnAdapter` config.
4. `src/universal_agent/execution_engine.py`
   - Added scoped runtime env override context manager for per-turn memory behavior.
   - Implemented mode-to-runtime mapping:
     - `off` disables memory/session memory.
     - `session_only` keeps session indexing while blocking long-term persistence.
     - `selective` enables long-term writes only for allowlisted tags.
     - `full` enables production-style memory persistence.
5. `src/universal_agent/feature_flags.py`
   - Added runtime flags:
     - `memory_runtime_tags()` (`UA_MEMORY_RUN_TAGS`)
     - `memory_long_term_tag_allowlist()` (`UA_MEMORY_LONG_TERM_TAG_ALLOWLIST`)
6. `src/universal_agent/memory/orchestrator.py`
   - Added runtime tag decoration for writes.
   - Added long-term tag-allowlist enforcement before persistence.
7. `src/universal_agent/ops_service.py`
   - Added session summary field `memory_mode` (derived from `session_policy.json` when present).
   - Added backend filtering for `memory_mode`.
8. `web-ui/components/dashboard/SessionGovernancePanel.tsx`
   - Added memory governance controls:
     - mode selector,
     - session memory enabled toggle,
     - editable memory tags,
     - editable long-term allowlist.
9. `web-ui/components/OpsDropdowns.tsx`
   - Added memory mode visibility in session cards.
   - Added memory mode filter in session directory view.
10. `tests/memory/test_memory_policy_runtime.py`
   - Added tests for runtime memory env override mapping and selective-write enforcement.
11. `tests/gateway/test_ops_api.py`
   - Extended session policy tests for memory defaults/patch behavior.
   - Added ops session filtering test for `memory_mode`.

### Test Gate

1. `session_only` keeps local/session behavior without long-term writes.
2. `selective` writes only tagged runs.
3. Semantic retrieval remains available for enabled stores.

### Phase 6 Validation Results (Executed)

1. `uv run pytest -q tests/memory/test_memory_policy_runtime.py`
   - Passed (`3 passed`).
2. `uv run pytest -q tests/gateway/test_ops_api.py`
   - Passed (`13 passed`).
3. `uv run pytest -q tests/memory/test_memory_policy_runtime.py tests/gateway/test_ops_api.py tests/gateway/test_session_idempotency.py tests/gateway/test_session_dropin.py tests/gateway/test_gateway.py tests/api/test_agent_bridge_selection.py`
   - Passed (`39 passed`, `4 warnings`).
4. `npm --prefix web-ui run lint`
   - Passed with no new errors; existing unrelated warnings remain in `web-ui/app/page.tsx`.

### Phase 6 Gate Status

`PASS`

---

## Phase 7: Observability and Rollout Gating

### Objectives

1. Measure whether session continuity actually improved.
2. Gate rollout on concrete operational metrics.

### Implementation Tasks

1. Metrics:
   - resume success rate,
   - accidental-new-session rate,
   - drop-in attach success,
   - duplicate-turn prevention count.
2. Structured logs for attach/detach/lock/idempotency decisions.
3. Alert thresholds for regressions.

### Code Changes (Executed)

1. `src/universal_agent/gateway_server.py`
   - Added observability counters for:
     - session creation,
     - websocket attach attempts/success/failure,
     - resume attempts/success/failure,
     - busy-turn rejections,
     - duplicate-turn suppressions.
   - Added derived metrics:
     - `resume_success_rate`,
     - `attach_success_rate`,
     - `duplicate_turn_prevention_count`.
   - Added threshold-based continuity alerts and status:
     - `continuity_status` (`ok|degraded`),
     - `alerts[]` with threshold comparisons for success rates and failure counts.
   - Added notification feed integration for continuity health transitions:
     - emits `continuity_alert` notifications when new alert conditions activate,
     - emits `continuity_recovered` notifications when alert conditions resolve,
     - deduplicates active alerts to avoid notification spam.
   - Added ops endpoint:
     - `GET /api/v1/ops/metrics/session-continuity`.
2. `tests/gateway/test_ops_api.py`
   - Added `test_ops_session_continuity_metrics_endpoint` to verify metric emission and endpoint payload shape/values.
   - Added `test_ops_session_continuity_metrics_alerts` for degraded/alerted threshold behavior.
   - Added `test_continuity_alert_notifications_are_emitted_and_deduped`.
3. `web-ui/components/OpsDropdowns.tsx`
   - Added `SessionContinuityWidget` with auto-refresh and manual refresh against `/api/v1/ops/metrics/session-continuity`.
   - Surfaced key operational signals in UI:
     - resume success rate,
     - attach success rate,
     - duplicate prevention count,
     - failure counters.
   - Surfaced threshold status and alert messages in the widget.
4. `web-ui/app/page.tsx`, `web-ui/app/dashboard/settings/page.tsx`
   - Wired continuity widget into primary chat shell sidebar and settings dashboard.
5. `src/universal_agent/gateway_server.py`
   - Extended dashboard notification status transitions to include `snoozed`.
   - Added snooze-expiry reactivation logic (`snoozed` -> `new` when expiry is reached).
   - Added bulk notification status endpoint for dashboard operations.
6. `web-ui/app/dashboard/page.tsx`
   - Added notification action controls:
     - continuity alerts: `Acknowledge`, `Snooze`, `Dismiss`,
     - other new notifications: `Mark Read`.
   - Wired controls to `PATCH /api/v1/dashboard/notifications/{id}`.
   - Added bulk continuity controls (`Ack All`, `Snooze All 30m`, `Dismiss All`) wired to bulk endpoint.
7. `tests/gateway/test_ops_api.py`
   - Extended dashboard notification API coverage for `snoozed` state + note persistence.
   - Added coverage for snooze expiry reactivation and bulk continuity update behavior.

### Test Gate

1. Synthetic reconnect/navigation load test.
2. Verified metric emission and dashboards.
3. Rollback path validated.

### Phase 7 Validation Results (Executed)

1. `uv run pytest -q tests/gateway/test_ops_api.py tests/memory/test_memory_policy_runtime.py tests/gateway/test_session_idempotency.py tests/gateway/test_session_dropin.py tests/gateway/test_gateway.py tests/api/test_agent_bridge_selection.py`
   - Passed (`44 passed`, `4 warnings`).
2. `npm --prefix web-ui run lint`
   - Passed with no new errors; existing unrelated warnings remain in `web-ui/app/page.tsx`.

### Phase 7 Gate Status

`PASS` (MVP observability endpoint + counters in place; alerting/dashboard wiring can iterate without backend contract changes).

---

## Rollback Strategy

1. Phase-level feature flags for attach/drop-in/idempotency.
2. Ability to disable dashboard attach UI without disabling core chat.
3. Resume hardening remains safe default and should not require rollback.

---

## Execution Notes

1. This plan intentionally prioritizes reliability and low operational risk over maximal feature breadth.
2. Full transcript rehydration can be layered later if tail mode plus bounded replay is insufficient.
3. Session memory and long-term memory remain explicitly separate and policy-controlled.
