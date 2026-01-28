# Event Streaming Issue: TOOL_CALL/TOOL_RESULT Events Not Reaching Web UI

**Date:** 2026-01-27  
**Status:** ✅ RESOLVED

---

## Solution Summary

The fix uses the **Claude SDK hook system** (`PreToolUse` and `PostToolUse` hooks) instead of parsing message streams:

1. **`hooks.py`** module provides:
   - `set_event_callback()` - Registers a global callback for emitting events
   - `emit_tool_call_event()` - Emits TOOL_CALL with deduplication
   - `emit_tool_result_event()` - Emits TOOL_RESULT with deduplication
   - `AgentHookSet.on_pre_tool_use_emit_event` - Hook that fires before tool execution
   - `AgentHookSet.on_post_tool_use_emit_event` - Hook that fires after tool execution

2. **`process_turn()`** registers the callback at the start:
   ```python
   hook_events.set_event_callback(event_callback)
   hook_events.set_event_start_ts(start_ts)
   hook_events.reset_tool_event_tracking()
   ```

3. The SDK hooks automatically call `emit_tool_call_event()` and `emit_tool_result_event()` during tool execution.

**Update (Text Streaming):**
Added `emit_text_event` to `hooks.py` and integrated it into `main.py`'s `TextBlock` handling to support streaming text responses to the Web UI.

**Key Insight:** The SDK's `PreToolUse`/`PostToolUse` hooks are the correct interception point for tool events, not parsing `AssistantMessage` content blocks. Text streaming, however, requires explicit emission during `TextBlock` processing.

## Problem Summary

The Universal Agent's Web UI has an "Activity & Logs" panel that should display real-time tool execution events. Currently, this panel remains empty (showing "0 events" and "No activity recorded yet") even when the agent successfully executes tools in the backend.

**Key observation:** The agent IS working correctly - tool execution output appears in `run.log` files, and final responses are delivered. But the **real-time streaming of `TOOL_CALL` and `TOOL_RESULT` events** to the UI is broken.

---

## Architecture Overview

```
Frontend (React/Next.js)           API Server                    Gateway Server
┌─────────────────────────┐       ┌─────────────────────┐       ┌─────────────────────────────┐
│ CombinedActivityLog.tsx │◄──────│ websocket_agent()   │◄──────│ ProcessTurnAdapter.execute()│
│ - reads toolCalls state │       │ - yields events     │       │ - uses event_callback       │
│ - reads logs state      │       │ via GatewayBridge   │       │ - queues → yields events    │
└─────────────────────────┘       └─────────────────────┘       └─────────────────────────────┘
                                                                              │
                                                                              ▼
                                                                   ┌─────────────────────────┐
                                                                   │ process_turn()          │
                                                                   │   → run_conversation()  │
                                                                   │   → ToolUseBlock handler│
                                                                   └─────────────────────────┘
```

**Files involved:**
- `web-ui/lib/store.ts` - Zustand store with `processWebSocketEvent()` handler for `tool_call`/`tool_result`
- `web-ui/components/CombinedActivityLog.tsx` - UI component reading from store
- `src/universal_agent/api/gateway_bridge.py` - API ↔ Gateway WebSocket bridge
- `src/universal_agent/gateway_server.py` - Gateway WebSocket endpoint
- `src/universal_agent/gateway.py` - `InProcessGateway.execute()` delegates to adapter
- `src/universal_agent/execution_engine.py` - `ProcessTurnAdapter.execute()` with callback mechanism
- `src/universal_agent/main.py` - `process_turn()` and `run_conversation()` functions

---

## What I've Tried

### 1. Verified Frontend Handlers Work
The `processWebSocketEvent()` in `store.ts` has proper handlers for `tool_call` and `tool_result` events:
```typescript
case "tool_call": {
  store.addToolCall({id, name, input, time_offset, status: "running"});
  break;
}
case "tool_result": {
  store.updateToolCall(tool_use_id, {result, status});
  break;
}
```
✅ Frontend code looks correct.

### 2. Verified ProcessTurnAdapter Yields Events
Added debug logging to `execution_engine.py`:
```python
def event_callback(event: AgentEvent) -> None:
    print(f"DEBUG ProcessTurnAdapter: event_callback received type={event.type}")
    event_queue.put_nowait(event)

# ... later ...
print(f"DEBUG ProcessTurnAdapter: yielding event type={event.type}")
yield event
```

**Debug output shows:**
```
DEBUG ProcessTurnAdapter: event_callback received type=EventType.STATUS
DEBUG ProcessTurnAdapter: yielding event type=EventType.STATUS
DEBUG ProcessTurnAdapter: event_callback received type=EventType.TEXT
DEBUG ProcessTurnAdapter: event_callback received type=EventType.ITERATION_END
```

❌ **NO `EventType.TOOL_CALL` or `EventType.TOOL_RESULT` events are being received by the callback!**

### 3. Added event_callback Threading to run_conversation()
Modified `main.py` to:
1. Add `event_callback` parameter to `run_conversation()` signature
2. Define `emit_event()` helper inside `run_conversation()`
3. Call `emit_event(AgentEvent(type=EventType.TOOL_CALL, ...))` after `ToolUseBlock` processing
4. Call `emit_event(AgentEvent(type=EventType.TOOL_RESULT, ...))` after `ToolResultBlock` processing
5. Updated `process_turn()` to pass `event_callback` to `run_conversation()`

**Added debug:**
```python
print(f"DEBUG run_conversation: About to emit TOOL_CALL for {block.name}, callback exists: {event_callback is not None}")
emit_event(AgentEvent(type=EventType.TOOL_CALL, data={...}))
```

❌ **This debug line NEVER appears in logs!**

---

## Root Cause Hypothesis

The `ToolUseBlock` processing code path in `run_conversation()` is NOT being reached when tools are executed. Looking at the message processing loop:

```python
# Line ~5086 in main.py
if isinstance(msg, AssistantMessage):
    for block in msg.content:
        if isinstance(block, ToolUseBlock):
            # THIS CODE IS NEVER REACHED
            tool_record = {...}
            emit_event(AgentEvent(type=EventType.TOOL_CALL, ...))
```

**Possible reasons:**
1. **Claude SDK auto-executes tools** - When using `ClaudeSDKClient`, tools may be executed internally and only the final result is returned, not intermediate `ToolUseBlock` messages
2. **Message iteration pattern** - The async iteration `async for msg in client.run_conversation(query):` may not yield `AssistantMessage` objects with `ToolUseBlock` content in the expected way
3. **SDK version difference** - The current SDK version may handle tools differently than expected

---

## Evidence

**Gateway log showing tool execution happened (but no TOOL_CALL event):**
```
📦 Tool Result (7791 bytes) +5.893s
   Preview: total 2.3M
   drwxrwxr-x  46 kjdragan kjdragan 4.0K Jan 27 20:20 .
   ...
```

This shows `ToolResultBlock` IS being processed (hence "📦 Tool Result" prints), but no `TOOL_CALL` event was emitted before it.

**Debug logs showing only STATUS/TEXT/ITERATION_END events flow through:**
```
DEBUG ProcessTurnAdapter: event_callback received type=EventType.STATUS
DEBUG ProcessTurnAdapter: event_callback received type=EventType.TEXT
DEBUG ProcessTurnAdapter: event_callback received type=EventType.ITERATION_END
```

---

## Key Code Locations

### 1. ToolUseBlock handling (where TOOL_CALL should emit)
**File:** `/home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py`  
**Lines:** ~5086-5155

### 2. ToolResultBlock handling (where TOOL_RESULT should emit)
**File:** `/home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py`  
**Lines:** ~5268-5340

### 3. ProcessTurnAdapter callback mechanism
**File:** `/home/kjdragan/lrepos/universal_agent/src/universal_agent/execution_engine.py`  
**Lines:** ~178-240

### 4. run_conversation function signature (modified)
**File:** `/home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py`  
**Lines:** ~4842-4867

---

## Questions to Investigate

1. **How does the Claude SDK yield tool-related messages?** Does `async for msg in client.run_conversation()` yield `AssistantMessage` with `ToolUseBlock` separately, or are tools auto-executed?

2. **Is there a different event source for tool calls?** Perhaps the SDK has a separate event mechanism for tool execution that we're not hooking into?

3. **Is the message loop iterating correctly?** Check if the `for msg in messages` loop is even being entered with `AssistantMessage` objects containing `ToolUseBlock`.

---

## Suggested Next Steps

1. Add logging at the START of the message processing loop to see what message types are being received:
   ```python
   for msg in messages:
       print(f"DEBUG: Received message type: {type(msg)}, content types: {[type(b) for b in getattr(msg, 'content', [])]}")
   ```

2. Check if there's a Claude SDK event subscription API that emits tool events separately from the message stream.

3. Compare with a working implementation (e.g., Claude's official examples) to see how tool events are captured.

---

## Environment

- **Python:** 3.13
- **Claude Agent SDK:** Installed via pip (bundled CLI)
- **Universal Agent:** Local development
- **Gateway Port:** 8002
- **API Server Port:** 8001
- **Frontend:** http://localhost:3000
