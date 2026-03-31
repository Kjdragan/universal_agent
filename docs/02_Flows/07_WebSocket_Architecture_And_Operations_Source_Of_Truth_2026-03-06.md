# 07. WebSocket Architecture and Operations Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for WebSocket usage in Universal Agent.

It explains which WebSocket surfaces exist, which ones are primary in current production, how they relate to each other, what environment knobs control them, and what operators should treat as active versus secondary or legacy.

## Executive Summary

Universal Agent currently uses WebSockets in **four distinct contexts**:

1. **Browser-facing dashboard session streaming** — the authenticated WebSocket surface exposed by the API server for the current web UI
2. **Gateway session streaming** — the canonical upstream session stream behind the browser-facing bridge
3. **AgentMail inbound email streaming** — a production WebSocket client connection from the gateway to AgentMail
4. **Legacy/simple web server chat streaming** — an older standalone WebSocket surface in `src/web/server.py`

These are not the same subsystem and should not be documented as one thing.

The current primary production WebSocket paths are:
- browser-facing `ws://.../ws/agent`
- browser-facing `ws://.../api/v1/sessions/{session_id}/stream`
- upstream gateway `ws://gateway.../api/v1/sessions/{session_id}/stream`
- AgentMail SDK WebSocket connection inside `AgentMailService`

## Canonical WebSocket Surfaces

## 1. Browser-Facing UI Session Streaming — Primary UI Transport

Primary implementation:
- `src/universal_agent/api/server.py`
- `web-ui/lib/websocket.ts`
- `web-ui/next.config.js`
- `web-ui/next.config.staging.js`

Primary endpoints:
- `GET WS /ws/agent`
- `GET WS /api/v1/sessions/{session_id}/stream`

Role:
- terminates the browser WebSocket on the authenticated API surface
- validates dashboard login cookies and session ownership
- creates or resumes sessions in the correct owner lane
- bridges or proxies live session traffic into the gateway runtime

### Current Architecture

The browser-facing UI does **not** connect directly to the token-gated gateway WebSocket on VPS.

Behavior:
- frontend connects to `/ws/agent` on the current browser origin
- local dev `:3000` rewrites `/ws/agent` to the API bridge on `:8001`
- staging rewrites `/ws/agent` to the API bridge on `:9001`
- the API server validates dashboard auth, resumes or creates the session, and then bridges into the gateway session runtime

For direct session attach/proxy compatibility, the API server also exposes `/api/v1/sessions/{session_id}/stream`.

It is responsible for:
- dashboard cookie validation
- owner-lane enforcement for resumed sessions
- upstream gateway stream proxying when `UA_GATEWAY_URL` is configured
- passive forwarding of background gateway events into the UI connection

## 2. Gateway Session Streaming — Canonical Upstream Session Stream

Primary implementation:
- `src/universal_agent/gateway_server.py`

Primary endpoints:
- `GET WS /ws/agent`
- `GET WS /api/v1/sessions/{session_id}/stream`

Role:
- provides the canonical session stream inside the runtime tier
- carries text updates, tool calls/results, status, approvals, and other live agent events

### Current Architecture

Gateway `/ws/agent` is a compatibility shim for callers that intentionally reach the gateway directly.

Behavior:
- browser traffic should normally arrive through the API server bridge, not directly here
- optional `session_id` query parameter is used to resume a session
- gateway delegates internally to the canonical route `/api/v1/sessions/{session_id}/stream`

The canonical route is `/api/v1/sessions/{session_id}/stream`.

It is responsible for:
- session attach/resume
- auth enforcement when session API auth is enabled
- allowlist checks
- connection registration by session
- send/broadcast behavior with timeouts
- backpressure/failure cleanup

### Connection Manager

The gateway maintains a connection manager that tracks:
- connection id -> socket
- session id -> connection ids

This enables:
- targeted session streaming
- broadcast to all clients attached to one session
- stale socket cleanup and metrics

### Auth and Safety

Gateway session WebSockets may require auth via `_require_session_ws_auth(...)`.

Important points:
- if session API auth is disabled, the socket can attach without token gating
- if enabled, invalid or missing token causes socket closure
- allowlist enforcement still applies after attach
- `LOCAL_WORKER` role can disable the WebSocket API entirely

## 3. Web UI WebSocket Client — Primary Browser Consumer

Primary implementation:
- `web-ui/lib/websocket.ts`

Role:
- browser-side WebSocket manager for the Next.js UI
- connects to the browser-facing API surface on the current origin
- resumes tab-scoped session ids
- handles reconnect, ping/pong, stale detection, and event dispatch to the store

### URL Resolution

Current client behavior:
- uses `NEXT_PUBLIC_WS_URL` if explicitly set
- otherwise derives `ws:` or `wss:` from browser location
- otherwise stays on the current browser origin and connects to `/ws/agent`
- on local development, that means the Next.js server on `:3000` forwards `/ws/agent` to the API bridge on `:8001`

### Browser-Side Reliability Features

The client currently implements:
- exponential reconnect with jitter
- connect timeout
- ping interval
- stale connection detection
- session id persistence in tab-scoped storage
- event fanout into UI state handlers

### Browser Env Knobs

Current browser-side WebSocket knobs in `.env.sample` include:
- `NEXT_PUBLIC_UA_WS_MAX_RECONNECT_ATTEMPTS`
- `NEXT_PUBLIC_UA_WS_RECONNECT_BASE_DELAY_MS`
- `NEXT_PUBLIC_UA_WS_RECONNECT_MAX_DELAY_MS`
- `NEXT_PUBLIC_UA_WS_CONNECT_TIMEOUT_MS`
- `NEXT_PUBLIC_UA_WS_PING_INTERVAL_MS`
- `NEXT_PUBLIC_UA_WS_STALE_AFTER_MS`

## 4. AgentMail WebSocket Listener — Primary Inbound Email Transport

Primary implementation:
- `src/universal_agent/services/agentmail_service.py`

Role:
- maintains an outbound persistent WebSocket connection from the gateway to AgentMail
- subscribes to Simone's inbox
- receives inbound email events in real time
- dispatches inbound messages to the `email-handler` agent through the hooks pipeline

### Why This Matters

This is a WebSocket **client** inside the backend, not a browser-facing WebSocket server.

It is the current authoritative inbound email path because it:
- does not require exposing a public webhook endpoint for email ingress
- works with outbound-only networking from the VPS
- provides low-latency message delivery
- includes reconnect logic and reply extraction before dispatch

### Runtime Behavior

The AgentMail service:
- opens the AgentMail WebSocket connection
- subscribes to the configured inbox
- listens for `MessageReceivedEvent`
- reconnects with exponential backoff and jitter on failure
- increments counters and exposes status through ops endpoints

### AgentMail WebSocket Env Knobs

Current AgentMail WebSocket knobs:
- `UA_AGENTMAIL_WS_ENABLED`
- `UA_AGENTMAIL_WS_RECONNECT_BASE_DELAY`
- `UA_AGENTMAIL_WS_RECONNECT_MAX_DELAY`

## 5. Shared Transport Tuning

Primary implementation:
- `src/universal_agent/timeout_policy.py`

This module centralizes runtime timeout and WebSocket tuning shared across gateway and related services.

It currently exposes tuning for:
- `UA_GATEWAY_WS_OPEN_TIMEOUT_SECONDS`
- `UA_GATEWAY_WS_CLOSE_TIMEOUT_SECONDS`
- `UA_GATEWAY_WS_PING_INTERVAL_SECONDS`
- `UA_GATEWAY_WS_PING_TIMEOUT_SECONDS`
- `UA_GATEWAY_WS_HANDSHAKE_TIMEOUT_SECONDS`
- `UA_WS_SEND_TIMEOUT_SECONDS`

This exists so WebSocket transport behavior remains discoverable and consistent rather than being scattered ad hoc.

## 6. Legacy / Secondary Surface: `src/web/server.py`

Implementation:
- `src/web/server.py`

Endpoint:
- `GET WS /ws/chat`

This file contains an older or simpler FastAPI-based WebSocket chat server that:
- accepts browser chat input
- spawns agent execution as a subprocess
- streams parsed output back to the socket

This is **not** the canonical current gateway transport for the main app.

Treat it as:
- secondary
- legacy-compatible
- useful historical context
- not the primary source of truth for the modern web UI transport

## Current Production Hierarchy

### Primary / Current

- browser-facing UI session streaming in `src/universal_agent/api/server.py`
- gateway upstream session streaming in `src/universal_agent/gateway_server.py`
- Next.js browser client in `web-ui/lib/websocket.ts`
- AgentMail inbound WebSocket client in `src/universal_agent/services/agentmail_service.py`
- shared timeout/tuning in `src/universal_agent/timeout_policy.py`

### Secondary / Legacy / Specialized

- `src/web/server.py` `/ws/chat`

## Operational Health Signals

## Gateway UI WebSockets

Healthy indicators:
- successful connection to `/ws/agent`
- browser websocket reaches the API server and receives a `connected` event
- session attach/resume works
- no sustained reconnect storm in browser logs
- no repeated gateway send timeouts
- session state updates arrive in real time

Potential failure signs:
- UI loops between disconnected and connecting
- websocket auth closures
- stale connection evictions in gateway logs
- repeated `ws_send_failures` / `ws_stale_evictions`

## AgentMail WebSockets

Healthy indicators:
- AgentMail ops status shows `ws_enabled=true`
- AgentMail ops status shows `ws_connected=true`
- reconnect count is low/stable
- inbound email counters increase when test messages arrive

Potential failure signs:
- repeated reconnects
- persistent `last_error`
- inbound email not reaching `email-handler`

## Related Endpoints and Interfaces

Gateway session WebSockets:
- browser-facing `/ws/agent` on the API server
- browser-facing `/api/v1/sessions/{session_id}/stream` on the API server
- upstream `/ws/agent` on the gateway
- upstream `/api/v1/sessions/{session_id}/stream` on the gateway

AgentMail operational visibility:
- `GET /api/v1/ops/agentmail`

Hooks readiness often matters indirectly when AgentMail dispatches inbound mail:
- `GET /api/v1/hooks/readyz`

## Current Gaps and Follow-Up Items

1. **Multiple WebSocket surfaces need explicit classification**
   - the repo contains more than one WebSocket implementation
   - future docs and prompts should avoid describing them as a single channel

2. **Legacy surface cleanup**
   - `src/web/server.py` should remain documented, but future cleanup may decide whether it should stay supported

3. **Cross-doc consistency**
   - chat UI docs, email docs, and runtime docs should continue pointing to the correct WebSocket surface for each use case

4. **Observability expansion**
   - richer operator-facing metrics for gateway WebSocket failures would improve diagnosis of reconnect storms or stale sessions

## Source Files That Define Current Truth

Primary implementation:
- `src/universal_agent/api/server.py`
- `src/universal_agent/gateway_server.py`
- `web-ui/lib/websocket.ts`
- `web-ui/next.config.js`
- `web-ui/next.config.staging.js`
- `src/universal_agent/services/agentmail_service.py`
- `src/universal_agent/timeout_policy.py`

Secondary/legacy:
- `src/web/server.py`

Related docs:
- `docs/02_Flows/04_Chat_Panel_Communication_Layer.md`
- `docs/02_Flows/05_Activity_Log_Communication_Layer.md`
- `docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`

## Bottom Line

Universal Agent does not have one generic WebSocket subsystem. It has:
- a primary browser-facing UI session transport on the API server
- a canonical upstream gateway session stream behind that bridge
- a primary backend AgentMail inbound WebSocket client
- a legacy standalone chat WebSocket server

The canonical current WebSocket story is therefore:
- **API-terminated, gateway-backed session streaming for the web UI**
- **AgentMail WebSockets for inbound email**
- **shared timeout policy for transport tuning**
- **legacy `/ws/chat` documented, but not treated as the primary app transport**
