# 10. Phased Implementation Plan (Heartbeat + Telegram + Deployment)

This plan is intentionally cautious to protect the stability of the current system.

## Phase 0 — Safety baseline (no behavior change)
**Goal:** lock in safety and clarity before we change runtime behavior.

- [ ] Confirm heartbeat requirements and final prompt contract.
- [ ] Decide whether to introduce a new `EventType.HEARTBEAT` or `STATUS.kind="heartbeat"`.
- [ ] Add a feature flag / config toggle for heartbeat (default **off**).
- [ ] Identify success criteria and regression checks for each phase.

**Testing gate:**
- Run the existing CLI and gateway flows to confirm baseline parity still holds.

---

## Phase 1 — WebSocket broadcast foundation
**Goal:** enable server push without changing engine execution logic.

**Implementation:**
1. Extend `ConnectionManager` to map `session_id -> set[WebSocket]`.
2. Add `broadcast(session_id, event)` helper.
3. Keep existing `execute` flow unchanged.
4. Emit a test “server notice” event on connection to validate broadcast.

**Testing gate:**
- New unit/integration test for broadcast to multiple clients.
- Validate existing web UI still works for execute streaming.

---

## Phase 2 — Heartbeat MVP (no delivery)
**Goal:** run heartbeat on a schedule but only emit summary events.

**Implementation:**
1. Add a `HeartbeatService` in `gateway_server` lifespan.
2. Add workspace `HEARTBEAT.md` read + empty-file skip.
3. Implement `UA_HEARTBEAT_OK` suppression.
4. Emit heartbeat summary events via broadcast.

**Testing gate:**
- Manual run: verify heartbeat fires and summary event is received.
- Confirm no interference with normal queries.

---

## Phase 3 — Heartbeat delivery and routing
**Goal:** allow heartbeat to deliver messages to supported channels.

**Implementation:**
1. Add delivery routing rules (`last`, `explicit`, `none`).
2. Add visibility policy (showOk/showAlerts/indicator).
3. Deduplicate repeated heartbeats (time-window suppression).

**Testing gate:**
- Verify delivery to web UI + optional Telegram.
- Validate no duplicate spam in quick succession.

---

## Phase 4 — Telegram revival (aligned with gateway)
**Goal:** bring back Telegram, but aligned with gateway for parity.

**Implementation:**
1. Keep bot service, but replace `AgentAdapter` with Gateway client.
2. Create per-user session mapping.
3. Stream final response; optionally send progress updates.

**Testing gate:**
- Telegram `/agent` -> gateway -> response parity with web UI.

---

## Phase 5 — Telegram enhancements
**Goal:** improve UX beyond revival.

**Implementation ideas:**
- `/status` shows latest run, heartbeat state.
- Cancellation (`/cancel`).
- Summary mode for long outputs.
- Rate limiting / guardrails for family use.

**Testing gate:**
- Manual Telegram regression tests + message length edge cases.

---

## Phase 6 — Deployment hardening (Railway)
**Goal:** stable remote hosting for web UI + Telegram + heartbeat.

**Implementation:**
1. Define Railway services (gateway + Telegram bot, or single service).
2. Configure environment variables + webhook URL.
3. Validate WebSocket stability.
4. Confirm persistence for `runtime.db` + workspaces.

**Testing gate:**
- Run overnight heartbeat test (>= 6 hours).
- Confirm Telegram continues to receive responses.

---

## Phase 7 — Multi-user support (family scale)
**Goal:** safe multi-user support without major complexity.

**Implementation:**
1. Allowlist enforcement (already present in Telegram bot).
2. Per-user session mapping.
3. Optional user preference storage (quiet hours, heartbeat opt-in).

**Testing gate:**
- Simulate 2–3 concurrent users and confirm isolation.

---

## Phase 8 — Post-launch stability
**Goal:** avoid regressions and establish operational confidence.

- Add structured logging for heartbeat events.
- Track failures and retries.
- Verify costs and performance within target.

---

## Summary of risk containment
- Heartbeat is **feature-flagged** and off by default.
- WebSocket broadcast is introduced in isolation before any heartbeat runs.
- Telegram revival happens **after** core heartbeat functionality is stable.
- Deployment changes are validated last, after functionality is proven locally.
