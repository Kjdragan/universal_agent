# 06. WebSocket Broadcast + Option C (SSE/Polling) Analysis

## 1. Current gateway streaming behavior
`gateway_server.py` implements a **request-scoped stream**:
- A client connects to `/api/v1/sessions/{session_id}/stream`.
- The server waits for an inbound `{type: "execute"}` message.
- It streams **only** the events produced by that execution.
- It then emits `query_complete`.

See: `src/universal_agent/gateway_server.py` @/home/kjdragan/lrepos/universal_agent/src/universal_agent/gateway_server.py#285-350.

This is a **command/response stream**, not a session-wide pub/sub channel.

## 2. Option A (recommended): add broadcast to WebSocket
### 2.1 Concept
Evolve the WS endpoint into a **session stream**:
- Keep the existing `execute` message path (backward compatible).
- Add server-side broadcast to all connections subscribed to a session.
- Heartbeat events can then be delivered **proactively** without a user trigger.

### 2.2 Why it fits UA
- Uses the same execution engine (`InProcessGateway` + `ProcessTurnAdapter`).
- No new transport; keeps latency minimal (single WS hop).
- Works for **multiple UI surfaces** (web, terminal client, future apps).

### 2.3 Minimal structural change
Add a connection registry keyed by `session_id`, not just connection ID:
- `connections_by_session: dict[str, set[WebSocket]]`
- `broadcast(session_id, message)`

## 3. Option C: add SSE or polling alongside WS
Option C means **keeping the WS execute stream as-is**, and adding a second mechanism for proactive server pushes.

### 3.1 Variant C1 — SSE (Server-Sent Events)
- **Pros**: One-way push channel, simpler than full WS.
- **Cons**: Still a second transport to implement, no bidirectional commands.
- **Latency**: low; good for “live updates”.
- **Complexity**: moderate (new endpoint, event formatting, reconnect logic).

### 3.2 Variant C2 — Polling
- **Pros**: simplest infra, works everywhere.
- **Cons**: higher latency + more load, feels “less alive”.
- **Latency**: bounded by poll interval (e.g., 2–10s).
- **Complexity**: low in code, but higher in UX cost.

## 4. Is Option C an upgrade or downgrade?
### Short answer: **C is a downgrade unless you need it as a compatibility fallback.**

Reasoning:
- It adds **another transport** without removing WS.
- It introduces **duplicated event formatting** and delivery paths.
- It **does not improve latency** over WebSockets; polling is worse.
- It **does not improve multi-session support** beyond what A already provides.

The only case where C is *better* is if you must support environments where WebSockets are unreliable or blocked and you need a fallback.

## 5. Multi-session + multi-user implications
You asked about multiple sessions and small multi-user support (3–4 users).

### With Option A (WS broadcast)
- Multiple sessions are straightforward: each session maps to a separate channel.
- Multiple users can be handled by **session scoping** and **user/channel auth**.
- One gateway process can serve multiple simultaneous sessions.

### With Option C
- You still need session scoping.
- You now need to duplicate it across SSE/polling endpoints.
- No inherent benefit for multi-session; just extra plumbing.

## 6. Recommendation
- **Primary path**: Option A (WebSocket broadcast) for low-latency, unified transport, and simpler reasoning.
- **Optional fallback**: Add Option C only if you discover real deployment constraints (firewalls/proxies) that make WS unreliable.

## 7. Proposed migration plan (if needed)
1. Implement broadcast inside `gateway_server.py` with minimal changes.
2. Wire heartbeat to broadcast summary events.
3. Only add SSE/polling if telemetry shows WS instability in real deployments.
