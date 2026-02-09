# 12 Multi-Interface Session Surface Implementation Plan (2026-02-09)

## 1. Purpose

Define and implement a stable multi-interface session model so Universal Agent works cleanly across:

1. Full-screen web chat.
2. Web dashboard operations pages.
3. Telegram single-thread interface.
4. Remote desktop/browser access to the same web stack.

This document is the source plan and execution log for this workstream.

## 2. Problem Statement

Current behavior is functionally close, but still carries avoidable complexity:

1. Dashboard `Chat` page embedded `/` in an iframe, which destroys and recreates chat surface sockets during page switches.
2. Session pointer persistence in web UI used a global browser key, which can cause tab collisions.
3. Dashboard controls could issue attach actions without explicitly moving the operator into the full-screen chat surface.
4. Continuity metrics are interpreted as user-facing health even when degradation is mostly attach transport churn.

## 3. Locked Design Decisions

1. Keep chat and dashboard as separate surfaces.
2. Chat is full-screen and owns interactive execution streaming.
3. Dashboard is control-plane and monitoring only.
4. Telegram remains a single-interface channel (no multi-page UX).
5. Session runtime is server-owned; UI clients only attach/detach.
6. Preserve existing heartbeat + cron + gateway architecture.

## 4. Interface Model

### 4.1 Web Chat Surface

1. Runs in a dedicated browser tab/window.
2. Attaches to a specific `session_id` when provided.
3. Streams live turns and accepts user input.

### 4.2 Web Dashboard Surface

1. Never embeds the full chat surface.
2. Opens or focuses dedicated chat tab/window for any session attach operation.
3. Continues to provide calendar/cron/session operational controls.

### 4.3 Telegram Surface

1. Remains one message-thread interface.
2. Uses the same server-owned session runtime semantics.
3. No separate dashboard pages are required for Telegram usage.

## 5. Phased Plan

### Phase 1 (Execute Now): Surface Separation + Attach Handoff Hardening

1. Remove dashboard embedded iframe chat.
2. Add dedicated chat-launch behavior from dashboard.
3. Support session attach via URL (`/?session_id=...&attach=tail`).
4. Move browser session pointer to tab-scoped storage to avoid cross-tab collisions.
5. Route dashboard-origin attach actions to open/focus full chat surface.

### Phase 2: Cross-Channel Session Directory and Routing Controls

1. Add explicit session origin/channel display consistency across all dashboard views.
2. Add session-open actions that always target dedicated chat surface.
3. Add remote-operator affordances (clear “attach as viewer/writer” semantics).

### Phase 3: Access and Ownership Hardening for Remote Login

1. Introduce authenticated dashboard access model for remote browser logins.
2. Preserve single-owner behavior while stubbing per-user ownership model.
3. Keep Telegram user/channel mapping independent but interoperable with dashboard session metadata.

### Phase 4: Continuity Metrics Rework

1. Shift from lifetime cumulative attach/resume ratios to rolling windows.
2. Separate transport churn from runtime health in operator UI.
3. Keep alerting useful without persistent false degraded states.

## 6. Phase 1 Implementation (Completed)

### 6.1 Code Changes

1. `web-ui/lib/chatWindow.ts`
   - Added shared chat-window launcher utilities:
   - `buildChatUrl(...)`.
   - `openOrFocusChatWindow(...)`.
2. `web-ui/lib/websocket.ts`
   - Migrated active session pointer from global localStorage behavior to tab-scoped storage.
   - Added legacy one-time migration path from old key to tab key.
   - Prevented cross-tab session pointer collisions.
3. `web-ui/app/page.tsx`
   - Added URL-driven attach bootstrap on mount.
   - Supports `session_id` and `attach=tail` query params before socket connect.
4. `web-ui/components/OpsDropdowns.tsx`
   - Dashboard-origin attach actions now open/focus dedicated full chat window instead of silently attaching in-place.
5. `web-ui/app/dashboard/chat/page.tsx`
   - Replaced iframe embed with `Chat Launcher` control panel.
   - Added open/focus actions and session list selector.
6. `web-ui/app/dashboard/layout.tsx`
   - Renamed nav label from `Chat` to `Chat Launch` for clarity.

### 6.2 Validation Gate for Phase 1

1. Frontend lint must pass.
2. Existing gateway session continuity tests must pass.
3. Existing session drop-in and idempotency tests must pass.

## 7. Phase 1 Exit Note

Phase 1 completed and handed off to Phase 2 execution.

## 8. Phase 2 Implementation (Completed)

### 8.1 Code Changes

1. `web-ui/lib/sessionDirectory.ts`
   - Added a shared session directory loader used by dashboard surfaces.
   - Primary source: `/api/v1/ops/sessions` (includes `source`, `channel`, `owner`, `memory_mode`).
   - Fallback source: `/api/sessions` for local-mode resilience.
2. `web-ui/lib/chatWindow.ts`
   - Added `role` support (`writer` or `viewer`) in chat launch URLs.
3. `web-ui/app/page.tsx`
   - Added viewer-mode handling from URL (`role=viewer`).
   - Viewer mode is read-only in chat (input/send disabled; explicit banner shown).
4. `web-ui/app/dashboard/chat/page.tsx`
   - Switched session loading to shared session directory data.
   - Added explicit attach role selector (`writer` / `viewer`) before opening full chat.
   - Added cross-channel/session metadata display (source, owner, memory mode).
5. `web-ui/app/dashboard/page.tsx`
   - Added a `Session Directory` panel with cross-channel session cards.
   - Added direct `Open Writer` and `Open Viewer` actions per session.
6. `web-ui/app/dashboard/cron-jobs/page.tsx`
   - Added session extraction from cron metadata/workspace.
   - Added `Open Session` action for jobs bound to a known session.

### 8.2 Validation

1. `npm --prefix web-ui run lint`
   - Passed with existing pre-existing warnings only in `web-ui/app/page.tsx`.
2. `npm --prefix web-ui run build`
   - Passed.
3. `uv run pytest -q tests/gateway/test_session_dropin.py tests/gateway/test_session_idempotency.py tests/gateway/test_gateway.py`
   - Passed (`20 passed`).

## 9. Phase 3 Implementation (Completed)

### 9.1 Code Changes

1. `web-ui/lib/dashboardAuth.ts`
   - Added signed dashboard session cookie model (`ua_dashboard_auth`).
   - Added `UA_DASHBOARD_AUTH_ENABLED`, `UA_DASHBOARD_PASSWORD`, `UA_DASHBOARD_OWNER_ID`, and `UA_DASHBOARD_SESSION_TTL_SECONDS` support.
   - Added single-owner normalization with a future-safe owner ID stub.
2. `web-ui/app/api/dashboard/auth/login/route.ts`
   - Added login endpoint that validates password (when enabled), issues signed cookie, and binds owner identity.
3. `web-ui/app/api/dashboard/auth/logout/route.ts`
   - Added logout endpoint that clears dashboard auth cookie.
4. `web-ui/app/api/dashboard/auth/session/route.ts`
   - Added session introspection endpoint for dashboard UI auth gating.
5. `web-ui/app/api/dashboard/gateway/[...path]/route.ts`
   - Added authenticated server-side proxy for dashboard traffic to gateway.
   - Injects ops credential on server side (`UA_DASHBOARD_OPS_TOKEN` fallback `UA_OPS_TOKEN`).
   - Keeps ops token out of browser code.
   - Applies owner filter default for `ops/sessions` and `ops/calendar/events` to preserve single-owner behavior while stubbing per-user model.
6. `web-ui/app/dashboard/layout.tsx`
   - Added dashboard auth gate UI (session check, sign in, sign out).
   - Blocks rendering dashboard operational panels until authenticated.
7. Dashboard/ops client fetch migration to proxy:
   - `web-ui/app/dashboard/page.tsx`
   - `web-ui/app/dashboard/cron-jobs/page.tsx`
   - `web-ui/lib/sessionDirectory.ts`
   - `web-ui/components/OpsDropdowns.tsx`
   - `web-ui/components/OpsPanel.tsx`
   - `web-ui/components/dashboard/SessionGovernancePanel.tsx`
   - Removed browser-side `NEXT_PUBLIC_UA_OPS_TOKEN` usage from operational fetch paths.

### 9.2 Operator Notes

1. Telegram flow remains unchanged and independent.
2. Dashboard remote login is now first-class and can be enabled without changing gateway API surface.
3. Owner scoping is currently single-owner default with explicit stubbing for multi-user expansion.

## 10. Validation

1. `npm --prefix web-ui run lint`
   - Pass (2 existing warnings in `web-ui/app/page.tsx`, no new errors).
2. `npm --prefix web-ui run build`
   - Pass.
3. `uv run pytest -q tests/gateway/test_session_dropin.py tests/gateway/test_session_idempotency.py tests/gateway/test_gateway.py`
   - Pass (`20 passed`).

## 11. Phase 4 Execution Trigger

Phase 4 execution was authorized and completed in the same workstream.

## 12. Phase 4 Implementation (Completed)

### 12.1 Code Changes

1. `src/universal_agent/gateway_server.py`
   - Added rolling-window continuity event tracking (`_continuity_metric_events`) with bounded retention.
   - Added windowed continuity configuration:
   - `UA_CONTINUITY_WINDOW_SECONDS`
   - `UA_CONTINUITY_RATE_MIN_ATTEMPTS`
   - `UA_CONTINUITY_EVENT_RETENTION_SECONDS`
   - `UA_CONTINUITY_EVENT_MAXLEN`
   - Reworked continuity snapshot from lifetime-rate math to rolling-window math.
   - Added explicit status split:
   - `transport_status` (attach/resume transport health)
   - `runtime_status` (runtime fault indicator lane)
   - Removed legacy aggregate field (`continuity_status`) to keep the model single-source and non-duplicative.
   - Added `window` payload in metrics response for operator visibility.
2. `web-ui/components/OpsDropdowns.tsx`
   - Extended continuity metric typing for rolling-window fields and split statuses.
   - Updated `SessionContinuityWidget` to show:
   - rolling window label
   - runtime status vs transport status independently
   - window failure counts
   - lifetime counters separately for context
   - Updated alert display with scope marker.
3. `tests/gateway/test_ops_api.py`
   - Added fixture reset for rolling continuity event deque.
   - Updated continuity assertions for split status fields and window payload.
   - Added new rolling-window behavior test to ensure stale events do not keep status degraded.
   - Updated continuity recovery dedupe test to clear rolling event buffer during forced recovery path.

### 12.2 Validation

1. `uv run pytest -q tests/gateway/test_ops_api.py`
   - Pass (`30 passed`).
2. `uv run pytest -q tests/gateway/test_session_dropin.py tests/gateway/test_session_idempotency.py tests/gateway/test_gateway.py`
   - Pass (`20 passed`).
3. `npm --prefix web-ui run lint`
   - Pass with existing pre-existing warnings in `web-ui/app/page.tsx`.
4. `npm --prefix web-ui run build`
   - Pass.

## 13. Next Execution Step

Run a live dashboard smoke against real gateway traffic and tune continuity window thresholds for your operating cadence.
