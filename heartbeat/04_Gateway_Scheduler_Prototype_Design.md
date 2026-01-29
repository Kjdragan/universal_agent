# 04. Gateway Scheduler Prototype Design

## 1. Objective
Draft a minimal prototype design that integrates a proactive heartbeat scheduler into the **gateway server lifecycle** without creating a second execution engine.

## 2. Integration point (where it plugs in)
`src/universal_agent/gateway_server.py` defines a FastAPI `lifespan()` context manager that:
- Creates the workspace root directory
- Initializes the runtime DB connection for the execution engine

Prototype integration:
- Create a `HeartbeatService` (scheduler + wake coalescer) inside `lifespan()`.
- Start it after DB setup.
- Stop it during shutdown.

## 3. Required gateway capability: server push
### 3.1 Current behavior
Gateway WebSocket streams events only inside the request loop when the client sends an `execute` message.

### 3.2 Heartbeat requirement
Heartbeats are proactive; the gateway must be able to:
- Emit events to connected clients **without an inbound execute**.

Prototype approach:
- Add a server-side `broadcast_to_session(session_id, event)` primitive.
- Heartbeat uses it to deliver:
  - A summarized heartbeat event (plus optional text content)
  - Or “indicator-only” events

## 4. Scheduler design (minimal)
### 4.1 Types
- `HeartbeatService`
  - owns an asyncio task
  - manages per-session schedules
  - exposes `request_wake(session_id, reason)`

- `HeartbeatSessionState`
  - `session_id`
  - `workspace_dir`
  - `every_seconds`
  - `next_due_at`
  - `running` flag
  - `pending_reason`
  - `last_sent_at`, `last_sent_text_hash` (dedupe)

### 4.2 Run loop (conceptual)
- Maintain a min-heap (or simple scan) of `next_due_at` values.
- Sleep until next due.
- When due:
  - enqueue wake request(s)
  - coalesce
  - run heartbeat if session engine idle

## 5. Executing a heartbeat turn
### 5.1 Use the existing execution engine
Use the same path as normal requests:
- `InProcessGateway.execute(session, GatewayRequest(user_input=<heartbeat prompt>))`

### 5.2 Heartbeat prompt construction
- Default prompt includes instruction to read `HEARTBEAT.md`.
- If missing, heartbeat still runs (model can decide).
- If present but effectively empty, we skip to save tokens.

## 6. Gating + retry behavior
Mirroring Clawdbot’s `requests-in-flight`:
- If a session is already processing a request, heartbeat run should:
  - return `skipped: busy`
  - schedule retry soon (e.g., 1s → 5s backoff)

## 7. Delivering results
Two layers:

### 7.1 Engine-level events
Stream the normal engine events to the UI if desired.
- In practice: UI might only want the summary.

### 7.2 Heartbeat summary event (recommended)
Emit one stable “heartbeat summary” event with:
- status (sent/skipped/failed)
- preview
- trace id
- duration

## 8. Cancellation and shutdown
- On gateway shutdown:
  - cancel scheduler task
  - ensure no new heartbeat runs start
  - allow in-flight engine runs to drain or cancel (policy choice)

## 9. Minimal test plan (design-level)
- **Unit**: schedule math (next due computation)
- **Unit**: coalescer (multiple wakes collapse)
- **Integration**: start gateway → create session → wait for tick → assert heartbeat event emitted
- **Integration**: while a normal execute is running, heartbeat should skip and retry

## 10. Open questions
- Should heartbeats be per-session by default, or should there be a single “main session” target?
- How do we define “last channel” once Telegram/Slack are connected?
- Do we store heartbeat config in env vars first, or introduce a config file format?

