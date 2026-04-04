# Active Flow System Breakdown

This document provides a technical diagnostic overview of the "Active Agent Flow" visualization system located in the `web-ui` and its corresponding python backend. It outlines how the system is currently architected, traces the end-to-end event pipeline, and identifies multiple reasons why the active flow simulation may not be visualizing live agent sessions properly.

## 1. System Architecture overview

The active flow system connects a React-based Next.js frontend (`web-ui/components/agent-flow`) to the Python Unified Agent Gateway backend (`src/universal_agent/gateway_server.py`) through a WebSocket stream.

**Data Flow:**
1. Next.js (`use-ua-bridge.ts`) starts a continuous WebSocket connection targeting `/ws/agent?session_id=global_agent_flow`.
2. The Gateway backend intercepts the `global_agent_flow` target, bypassing standard session-resume checks and attaching the socket to a mock global session.
3. Every time an agent or runtime action occurs, the backend calls `manager.broadcast(session_id, data)`.
4. In `manager.broadcast()`, the event payload is intentionally delivered to both the specific `session_id` group AND the `global_agent_flow` group.
5. In the frontend, `mapBackendEventToSimulation()` attempts to coerce backend events to standard `SimulationEvent` formats.
6. The `useAgentSimulation` React hook consumes these simulation events and runs a 60fps d3-force physics layout (`frameRef`) decoupled from standard React renders to visualize interconnected agents and tools.

---

## 2. Identified Failure Points

The integration between the legacy UI visualization concepts and the modernized `universal_agent` runtime has diverged. The system appears broken due to a combination of payload mapping bugs and outdated event-type expectations.

### Issue 1: `session_id` Omission in the Event Payload
In `gateway_server.py/agent_event_to_wire`, an instance of `AgentEvent` is serialized into `{ type, data, timestamp, time_offset }`. **It does not inject the `session_id` into the JSON payload.**

When a `global_agent_flow` client receives an event:
- It receives an event about *some* session.
- But because `payload.session_id` is missing or top-level `session_id` is absent, the frontend (`use-ua-bridge.ts:167`) computes `const sessionId = payload.session_id; // undefined`.
- When passed into `useAgentSimulation.ts:230`, the event checks `if (activeFilter && event.sessionId && event.sessionId !== activeFilter)`. With missing or undefined session IDs, the data becomes un-routable. The visualizer fails to associate it properly with a valid tracked session.

### Issue 2: Misalignment of Event Types
The UI acts upon extremely specific backend strings:
```typescript
   if (tType === 'iteration_start' || tType === 'system_event') { 
       return { type: 'agent_spawn' ... }
   }
   if (tType === 'plan_update' || tType === 'status') {
       return { type: 'message' ... }
   }
   if (tType === 'iteration_end' || tType === 'query_complete') {
       return { type: 'agent_complete' ... }
   }
```
However, the modern `universal_agent` runtime (Unified Runtime / Orchestrator) utilizes very different event types now. 
- While `system_event`, `status`, and `query_complete` still exist...
- Fundamental occurrences like `iteration_start` have largely vanished and been replaced by general purpose `agent_event`, `base64`, `action`, `vp_mission_event`, etc.
Because the mapper (`mapBackendEventToSimulation()`) rigidly maps only a subset of legacy events, crucial runtime executions are being silently dropped on the frontend. Thus, the visualization stays in the `"WAITING FOR AGENT SESSION"` state without spawning agent nodes on the canvas.

### Issue 3: Incomplete Session Initialization 
When a new backend event is processed on `global_agent_flow`, the UI intercepts valid connections via the message type `connected`. However, the live WebSocket event broadcasts might not be triggering the UI's `setSessions()` call recursively. If the frontend is waiting for an explicit `connected` frame for an active session to populate its active `sessions[]` list, the `global_agent_flow` listener never manually synthesizes active session list state unless it gets explicit notifications per session.

## 3. Recommended Fixes

If an AI or engineer is tasked with restoring this feature, please follow these implementation steps:

1. **Fix Backend Payload Injection**: Modify `ConnectionManager.broadcast` in `src/universal_agent/gateway_server.py` to ensure that `session_id` is appended to the root data structure so the `global_agent_flow` connection can deduce the origin session of the event.
   ```python
   # Inside manager.broadcast():
   if isinstance(data, dict):
       data["session_id"] = session_id
   ```
2. **Update UI Payload Reader**: Update `mapBackendEventToSimulation` in `web-ui/hooks/agent-flow/use-ua-bridge.ts` to map `sessionId = backendData.session_id || payload.session_id`.
3. **Audit Event Types**: Align UI `tType` checks with modern backend outputs. Ensure there's a strong fallback to render standard `action` executions or `text` as active agent nodes so it doesn't wait strictly for legacy `iteration_start`.
4. **Session Discovery**: Ensure the UI populates the `sessions` array dynamically if it starts receiving structural events for an unknown `session_id`.
