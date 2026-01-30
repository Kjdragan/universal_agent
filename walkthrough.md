# Walkthrough

## Phase 1 Verification: WebSocket Broadcast
**Date:** 2026-01-29  
**Phase:** 1 (WebSocket Broadcast Foundation)

### Summary
Implements the foundation for server-initiated broadcasts to multiple WebSocket clients sharing the same session. This is a prerequisite for the Heartbeat system, which needs to push status updates to the UI even when the agent is idle or busy.

### Changes
**File:** `src/universal_agent/gateway_server.py`  
- Updated `ConnectionManager` to track `session_id -> set[connection_id]`.
- Added `broadcast(session_id, message)` method.
- Added `broadcast_test` event type for verification.

### Verification
A new test script `tests/gateway/test_broadcast.py` was created to verify the behavior.

**Test Workflow**
1. Create a session via REST API.
2. Connect Client A via WebSocket.
3. Connect Client B via WebSocket.
4. Client A sends a `broadcast_test` message.
5. Gateway broadcasts `server_notice` to all connections (A and B).
6. Test passes if both clients receive the message.

**Result**
```
INFO - Creating session at http://localhost:8004/api/v1/sessions...
INFO - Session created: test_workspace_broadcast
INFO - Connecting Client A...
INFO - Client A connected.
INFO - Client B connected.
...
INFO - Client A sending 'broadcast_test'...
INFO - Client A received: server_notice
INFO - Client B received: server_notice
INFO - ✅ SUCCESS: Both clients received the broadcast event.
```

### Next Steps
Phase 2: Implement the Heartbeat Scheduler and Summary events (using the broadcast capability).

---

## Phase 2 Verification: Heartbeat Service
**Date:** 2026-01-29  
**Phase:** 2 (Heartbeat Implementation & Debugging)

### Summary
The Heartbeat Service was implemented to periodically wake up the agent. Initial failures were caused by missing environment variables for the Claude CLI subprocess and by suppressing the heartbeat summary event when the model replied `UA_HEARTBEAT_OK`.

### Fixes Applied
1) **Load `.env` in the Gateway server process** so the Claude CLI subprocess inherits API keys.  
   - File: `src/universal_agent/gateway_server.py`
2) **Always broadcast `heartbeat_summary`**, even when the response is `UA_HEARTBEAT_OK`.  
   - File: `src/universal_agent/heartbeat_service.py`
   - Adds `ok_only: true` to indicate suppression-worthy content but still emits the event.
3) **Add safety + observability** (non-invasive, does not change agent logic):
   - CLI stderr capture for debugging (shows trust/auth prompts or CLI errors).
   - Heartbeat execution timeout to prevent infinite waits.
   - File: `src/universal_agent/execution_engine.py` (stderr/debug flags)
   - File: `src/universal_agent/heartbeat_service.py` (timeout)

### Why this fixes the issue
- The gateway was launching the Claude CLI subprocess **without** API credentials (because `.env` was never loaded). The CLI would then stall during initialization. Loading `.env` ensures the subprocess inherits `ANTHROPIC_API_KEY`, `COMPOSIO_API_KEY`, etc.
- The test expects a `heartbeat_summary` event. Previously the code **suppressed** it when the model responded with `UA_HEARTBEAT_OK`, which caused the test to wait forever. Now the event is always emitted.
- The timeout + stderr capture are guardrails to ensure any future stalls are visible and do not block the scheduler indefinitely.

### Verification (Re-run)
**Test:** `tests/gateway/test_heartbeat.py`  
**Result:** ✅ PASSED (heartbeat_summary received)

**Repro Commands**
```bash
export UA_ENABLE_HEARTBEAT=1
export UA_GATEWAY_PORT=8021
uv run python -m universal_agent.gateway_server

export GATEWAY_URL=http://localhost:8021
uv run tests/gateway/test_heartbeat.py
```

**Expected Output**
```
INFO - Waiting for heartbeat_summary...
INFO - Received event: heartbeat_summary
INFO - ✅ SUCCESS: Heartbeat received: {...}
```

### If you still see a hang
Make sure the gateway process was restarted after the `.env` loading change and use:
```
UA_CLAUDE_CLI_STDERR=1
UA_CLAUDE_CLI_DEBUG=api,hooks
UA_HEARTBEAT_EXEC_TIMEOUT=45
```
Then check gateway logs for lines prefixed with `Claude CLI stderr:` to identify trust/auth prompts or CLI errors.
