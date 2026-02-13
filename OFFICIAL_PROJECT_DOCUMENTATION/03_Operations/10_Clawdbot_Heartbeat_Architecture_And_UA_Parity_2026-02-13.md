# Clawdbot Heartbeat Architecture, Flows, And UA Parity Plan (2026-02-13)

## Goal

Understand how **Clawdbot/OpenClaw** heartbeats actually drive proactive behavior (introspection, monitoring, wakeups), then identify where **Universal Agent (UA)** falls short and implement changes to reach parity (or better).

This report focuses on **heartbeat as the “wake + delivery + introspection entrypoint”**, not just “a timer that sends HEARTBEAT_OK”.

## Executive Summary

OpenClaw’s “heartbeat system” is best understood as three cooperating mechanisms:

1. **A coalesced wake mechanism** (`requestHeartbeatNow` + `runHeartbeatOnce`) that safely “nudges” the main agent lane when background work completes or when a scheduler wants attention.
2. **An ephemeral system-event queue** that captures important out-of-band facts (cron completion summaries, exec finished, channel connect/disconnect, hooks) and **prefixes them into the next model prompt**.
3. **A heartbeat runner** that periodically executes a “heartbeat poll” prompt (usually “Read HEARTBEAT.md … reply HEARTBEAT_OK if nothing”) and delivers only meaningful output, with strong suppression/dedupe safeguards.

In UA, we had implemented most of (1) and (3), and we also had a system-events queue, but the critical gap for proactive parity was:

- **System events were not being prefixed into the LLM prompt** (they were queued and attached to request metadata, but never consumed by the engine). This materially reduces proactive capability because the agent never “sees” exec/cron completions and monitor events unless the user explicitly restates them.

UA changes implemented on 2026-02-13:

- Plumbed `metadata.system_events` through the gateway and execution engine into `process_turn()` via env, and **prefixed them into the next complex-path prompt**.
- Forced the “complex/tool loop” path when system events exist (so the agent can act on them).
- Adjusted heartbeat scheduling to **consume** explicit-delivery windows with no connected targets (no backfill).

## OpenClaw / Clawdbot: Architecture And Flows

### Components (source map)

Heartbeat prompt/token utilities:
- `clawdbot/src/auto-reply/heartbeat.ts`
  - `HEARTBEAT_PROMPT`
  - `stripHeartbeatToken(...)` (suppresses ack-only replies)

Wake coalescing + retry:
- `clawdbot/src/infra/heartbeat-wake.ts`
  - `requestHeartbeatNow(...)` coalesces wake requests
  - retries when main lane is busy (`requests-in-flight`)

Heartbeat runner (“do a heartbeat turn + deliver if meaningful”):
- `clawdbot/src/infra/heartbeat-runner.ts`
  - `runHeartbeatOnce(...)`: performs a single heartbeat turn
  - `startHeartbeatRunner(...)`: schedules interval wakes across agents
  - Key behaviors:
    - active-hours gating
    - queue-size gating: skip if main lane has pending requests (`requests-in-flight`)
    - skip if `HEARTBEAT.md` exists but is effectively empty
    - exec-completion special prompt override (`EXEC_EVENT_PROMPT`)
    - “don’t keep session alive” behavior (restore prior `updatedAt`)
    - deliver suppression:
      - `showOk` / `showAlerts`
      - duplicate suppression (24h)
      - heartbeat-token suppression (ack-only)
    - emits `infra/heartbeat-events.ts` indicators

Ephemeral system events:
- `clawdbot/src/infra/system-events.ts`
  - `enqueueSystemEvent(...)`
  - `drainSystemEvents(...)`
  - `peekSystemEvents(...)`

System event prompt prefixing:
- `clawdbot/src/auto-reply/reply/session-updates.ts`
  - `prependSystemEvents(...)` drains queued events and prefixes:
    - `System: [timestamp] message`
  - filters out heartbeat-poll noise and other low-value events

Cron integration (generates system events + wakes):
- `clawdbot/src/cron/service/timer.ts`
  - for `sessionTarget=main`: enqueue a system event and `requestHeartbeatNow(...)`
  - for `sessionTarget=isolated`: run isolated turn, enqueue a summary system event back to main, optionally wake

### Flow 1: “Interval heartbeat”

1. `startHeartbeatRunner(...)` computes `nextDueMs` per heartbeat-enabled agent.
2. When due, it triggers `requestHeartbeatNow({ reason: "interval", coalesceMs: 0 })`.
3. `heartbeat-wake.ts` coalesces and calls the installed handler, which calls `runHeartbeatOnce({ reason: "interval" })`.
4. `runHeartbeatOnce(...)`:
   - skips if disabled or outside active hours
   - skips if main lane queue has work (`requests-in-flight`)
   - optionally skips if `HEARTBEAT.md` exists but is effectively empty
   - calls `getReplyFromConfig(..., { isHeartbeat: true })` with `Body = resolveHeartbeatPrompt(...)`
5. Response is normalized:
   - if response is empty or ack-only (`HEARTBEAT_OK`), treat as “ok-empty/ok-token”
   - otherwise deliver alerts (text/media) to resolved target channel
6. Deduping:
   - if identical alert text was delivered recently (24h), skip.

Key “why this works” point:
- **The heartbeat prompt is intentionally minimal**; the intelligence lives in:
  - `HEARTBEAT.md` (explicit tasks)
  - system events (out-of-band signals)
  - tools (sessions, filesystem, cron, messaging)

### Flow 2: “Wake heartbeat now because something completed”

Sources include:
- cron job completion
- hook completion
- exec completion (“Exec finished …”)
- channel connect/disconnect monitors

Flow:
1. A subsystem calls `enqueueSystemEvent("X happened", { sessionKey })`.
2. The subsystem also calls `requestHeartbeatNow({ reason: "..." })` (often `wakeMode: now`).
3. Heartbeat runner executes quickly, and the next prompt includes the system event prefix (via `prependSystemEvents(...)`).

This is the primary mechanism by which OpenClaw creates “proactive” behavior:
- background systems generate concise structured notifications
- heartbeat wakes the model so the model can interpret them and act

### Flow 3: “Cron -> Main session”

1. Cron timer determines due job.
2. For `sessionTarget=main`:
   - it resolves a short `systemEvent` payload string
   - enqueues it (system events)
   - wakes heartbeat
3. Heartbeat runner causes the agent to see the event next turn and act.

### Flow 4: “Cron -> Isolated session -> Summary to main”

1. Cron runs an isolated agent job (`payload.kind=agentTurn`).
2. On completion, it enqueues a summary system event into the main session.
3. Wakes main session via heartbeat.

This prevents “background work” from being silent; the main agent lane gets a concise “what happened”.

## UA: Current Architecture And Flows

### Components (source map)

Heartbeat service:
- `src/universal_agent/heartbeat_service.py`
  - per-session scheduler loop
  - per-session persisted state: `<workspace>/heartbeat_state.json`
  - overrides: `<workspace>/HEARTBEAT.json` (also `heartbeat.json`, `.heartbeat.json`)
  - delivery to connected UI sessions (websocket broadcast)
  - response suppression and dedupe
  - exec/cron completion prompt override (`EXEC_EVENT_PROMPT`)

Gateway session execution:
- `src/universal_agent/gateway.py`
- `src/universal_agent/execution_engine.py`
- `src/universal_agent/main.py`

System events queue (gateway server):
- `src/universal_agent/gateway_server.py`
  - `_enqueue_system_event(...)`, `_drain_system_events(...)`
  - drained on user execute requests (websocket) and by heartbeat runs

Cron integration:
- `src/universal_agent/cron_service.py`
  - enqueues system events on completion (if session metadata provides a target)
  - can wake heartbeat via `_cron_wake_callback(...)` in `gateway_server.py`

### What UA already matched well

- Heartbeat schedule + quiet hours.
- “Don’t spam” behavior: ok-token stripping, show_ok / show_alerts, dedupe windows.
- Exec completion special prompt exists in UA (concept matches OpenClaw).
- HEARTBEAT.md “effectively empty” skip behavior exists in UA.

### Critical missing link (pre-2026-02-13)

UA had a system events queue, but the engine did not consume it:

- `gateway_server.py` drained system events and put them in `GatewayRequest.metadata["system_events"]`
- `heartbeat_service.py` also injected `metadata["system_events"]`
- but `gateway.py` only propagated `source` and `memory_policy` into the execution engine
- and `main.py/process_turn()` had no mechanism to prefix system events into the prompt

Result:
- cron completions and monitor events often did not meaningfully influence agent behavior unless the user manually restated them.

## Parity Gap Analysis

### P0: System events must reach the model prompt

OpenClaw’s proactive power largely comes from:
- many subsystems enqueueing system events
- those events being prefixed into the next model prompt (and therefore “actionable”)

UA had the queue, but it wasn’t in the prompt. This is the highest-leverage parity fix.

### P1: Heartbeat prompt semantics / HEARTBEAT.md contract

OpenClaw default prompt is intentionally minimal.
UA’s prompt historically leaned more prescriptive (checkbox semantics and strict ack contract).

UA can keep checkbox semantics (it’s fine), but we should ensure:
- HEARTBEAT.md content style is compatible with the prompt
- we don’t accidentally make UA “ignore” tasks because they aren’t checkboxes

### P1: Main-lane backpressure semantics

OpenClaw explicitly skips heartbeat when the main lane has requests in flight (`requests-in-flight`) and retries soon.

UA currently uses `busy_sessions` at the heartbeat-service layer and a global gateway execution lock, but does not implement “requests-in-flight” semantics for heartbeat scheduling at the same granularity.

### P2: Proactive memory/reflection tooling

OpenClaw has an explicit “retain/recall/reflect” design direction (see `clawdbot/docs/experiments/research/memory.md`).

UA already has substantial memory infrastructure, but “proactive reflection jobs” and “prompt-visible memory snapshots” are a separate parity track (see existing UA docs: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/04_Clawdbot_Memory_Parity_Investigation.md`).

## UA Implementation Plan (Parity Track)

### Phase 1 (P0): System events prompt injection (implemented 2026-02-13)

1. Propagate `system_events` from `GatewayRequest.metadata` into the execution adapter.
2. Provide the engine with system events for that single turn (scoped env).
3. Prefix system events into the next complex-path prompt as `System: ...` lines.
4. Force the complex path when system events exist.

### Phase 2 (P1): Heartbeat backpressure + wake behavior

1. Add a “requests-in-flight” notion for UA (at least: skip heartbeat when the target session is currently running a user turn).
2. Add coalesced retry behavior on “busy main lane” (similar to `heartbeat-wake.ts`’s retry loop).

### Phase 3 (P1/P2): Proactive monitors + reflection jobs

1. Add a first-class “reflect” cron job template that:
   - reads recent transcripts / durable trace
   - updates memory summaries
   - writes stable “bank/” pages (entity/opinion pages) or UA-native equivalents
2. Ensure reflection artifacts are easy for the agent to read in later heartbeats.

## Changes Implemented In UA (2026-02-13)

Files changed:
- `src/universal_agent/gateway.py`
  - pass `metadata.system_events` into the adapter config (`_system_events`)
- `src/universal_agent/execution_engine.py`
  - serialize system events into `UA_SYSTEM_EVENTS_JSON`
  - provide `UA_SYSTEM_EVENTS_PROMPT` for convenience
- `src/universal_agent/main.py`
  - force complex path when system events exist
  - prefix system events into the initial complex-path query (first iteration)
- `src/universal_agent/heartbeat_service.py`
  - “no backfill” behavior for explicit-delivery windows with no connected targets (consume window and persist state)
  - “no backfill” behavior for scheduled windows when the session is busy (consume window; do not drop explicit wake requests)

## Next Validation Steps

1. Trigger a cron job with `metadata.target_session` set and `wake_heartbeat` enabled; verify the next heartbeat turn includes the cron completion event in the prompt and produces a meaningful response.
2. Trigger an exec completion system event (or any event containing “Exec finished”), wake heartbeat now, and confirm `EXEC_EVENT_PROMPT` behavior.
3. Confirm no heartbeat backfill behavior when explicit delivery targets are disconnected.
