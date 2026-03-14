# 05 Activity Log Communication Layer

This document describes the complete data pipeline that populates the **Activity Log** — the technical observability panel in the Web UI. It covers every layer from backend event sources through WebSocket transport, frontend store processing, and final React rendering in the `CombinedActivityLog` component.

---

## 1. Purpose & Design Principles

The Activity Log displays **operational detail** — what's happening under the hood during agent execution. It provides full technical observability of tool calls, tool results, hook activity, system status, and Python process output.

### What Belongs Here

| Content Type | Source Event | Example |
|-------------|-------------|---------|
| **Tool calls** | `TOOL_CALL` event | `mcp__composio__GMAIL_SEND_EMAIL` — input: `{to: "...", subject: "..."}` |
| **Tool results** | `TOOL_RESULT` event | Result: 2.3 KB, success |
| **Status/log events** | `STATUS` event (`is_log: true`) | "Step 2/4: Generating outline..." |
| **Hook activity** | `STATUS` event (from hooks) | "Hook: blocked 'WebSearch' for Primary Agent" |
| **Iteration summaries** | `ITERATION_END` event | "Iteration 2 complete — 5 tool calls, 12.3s" |
| **Python stdout/stderr** | `STATUS` event (intercepted) | Console output from in-process tools |
| **MCP/Composio activity** | `STATUS` event (via bridges) | "[Local Toolkit] Uploading attachment..." |

### What Does NOT Belong Here

- Agent speech / TEXT events (→ Chat Panel)
- Thinking blocks (→ Chat Panel)
- User messages (→ Chat Panel)

### Principle: Maximum Observability, No Duplication

Every technical event should appear in the Activity Log. Err on the side of too much detail — verbosity can be dialed back later. Each event appears **once** within this panel.

---

## 2. Backend Event Sources

The Activity Log is fed by multiple independent event sources, all converging on the same `STATUS`, `TOOL_CALL`, and `TOOL_RESULT` event types.

### 2.1 Hook-Emitted Events (`hooks.py`)

The `AgentHookSet` hooks emit events at key lifecycle points:

#### PreToolUse → `TOOL_CALL` Event

```python
async def on_pre_tool_use_emit_event(self, input_data, tool_use_id, context):
    emit_tool_call_event(
        tool_use_id=tool_use_id,
        tool_name=input_data.get("tool_name", ""),
        tool_input=input_data.get("tool_input", {}),
    )
```

Deduplication: `_EMITTED_TOOL_CALL_IDS_VAR` (ContextVar set) ensures each `tool_use_id` is emitted only once.

#### PostToolUse → `TOOL_RESULT` Event

```python
async def on_post_tool_use_emit_event(self, input_data, tool_use_id, context):
    emit_tool_result_event(
        tool_use_id=tool_use_id,
        is_error=bool(input_data.get("is_error")),
        tool_result=input_data.get("tool_result"),
    )
```

Deduplication: `_EMITTED_TOOL_RESULT_IDS_VAR` ensures each result is emitted once.

#### Hook Activity → `STATUS` Events

Significant hook activity emits status events for visibility:

- **Tool blocking**: `"Hook: blocked 'WebSearch' for Primary Agent (must delegate)"`
- **Composio SDK blocking**: `"Hook: blocked direct Composio SDK usage in Bash"`
- **Skill hints**: `"Hook: skill hint injected for PDF"`
- **Validation errors**: `"Hook: tool validation error detected"`

These are emitted via `emit_status_event()` with `is_log=True` so they appear in the Activity Log.

### 2.2 Stdout/Stderr Capture

Three mechanisms capture Python process output and route it to the Activity Log:

#### `StdoutToEventStream` (Context Manager — hooks.py)

Used by local toolkit and research bridge wrappers to capture in-process tool output:

```python
with StdoutToEventStream(prefix="[Local Toolkit]"):
    result_str = await original_pipeline(query, task_name)
```

Any `print()` or stdout writes within the block are emitted as `STATUS` events with `is_log: True` and the specified prefix.

**Used in**: `local_toolkit_bridge.py`, `research_bridge.py` — wraps every in-process tool execution.

#### `StdoutInterceptor` (log_bridge.py)

Replaces `sys.stdout` and `sys.stderr` with intercepting wrappers that:
1. Write to the original stream (preserving CLI output)
2. Emit each line as a `STATUS` event with `is_log: True`

**Used in**: Legacy `agent_core.py` path via `setup_log_bridge()`.

#### `LogBridgeHandler` (log_bridge.py)

A Python `logging.Handler` that bridges log records to `STATUS` events:

```python
bridge = LogBridgeHandler(agent.send_agent_event)
logging.getLogger("httpx").addHandler(bridge)
```

Captures HTTP client activity, Composio SDK logs, and other library-level logging.

### 2.3 Direct Event Emission (`main.py`)

The `run_conversation()` function also emits events directly via `hook_events`:

- **`emit_tool_call_event()`** — when `ToolUseBlock` is encountered in the SDK stream
- **`emit_tool_result_event()`** — when `ToolResultBlock` is processed

These use the same deduplication sets as the hook-emitted events, so there's no double-counting even though both `run_conversation()` and the hooks may attempt to emit for the same tool call.

### 2.4 Iteration Summaries

`ITERATION_END` events are emitted at the end of each agent turn with summary data:

```python
AgentEvent(
    type=EventType.ITERATION_END,
    data={
        "status": "complete",
        "duration_seconds": 12.3,
        "tool_calls": 5,
        "trace_id": "...",
    },
)
```

These are routed to the Activity Log in the frontend store (see Section 4).

---

## 3. Transport: WebSocket

All events flow through the same WebSocket channel as chat events:

```
emit_status_event() / emit_tool_call_event() / emit_tool_result_event()
  → _emit_event() → ContextVar callback
    → event_queue (asyncio.Queue)
      → gateway.execute() async generator
        → gateway_server.websocket_stream()
          → WebSocket JSON: {"type": "tool_call", "data": {...}, "timestamp": "..."}
```

The gateway server's `agent_event_to_wire()` function serializes events:

```python
def agent_event_to_wire(event: AgentEvent) -> dict:
    return {
        "type": event.type.value,
        "data": event.data,
        "timestamp": datetime.now().isoformat(),
        "time_offset": event.data.get("time_offset") if isinstance(event.data, dict) else None,
    }
```

---

## 4. Frontend Store Processing (`store.ts`)

The `processWebSocketEvent()` function routes each event type:

### 4.1 Tool Calls → `addToolCall()`

```typescript
case "tool_call": {
    // Tool calls go to the Activity Log only — do NOT finishStream().
    store.addToolCall({
        id: data.id,
        name: data.name,
        input: data.input,
        time_offset: data.time_offset,
        status: "running",
    });
    store.incrementToolCalls();
    break;
}
```

Tool calls are deduplicated by `id` in the store — if a duplicate `tool_use_id` arrives (reconnect, retry), the existing entry is updated rather than duplicated.

### 4.2 Tool Results → `updateToolCall()`

```typescript
case "tool_result": {
    store.updateToolCall(data.tool_use_id, {
        result: {
            tool_use_id: data.tool_use_id,
            is_error: data.is_error,
            content_preview: data.content_preview,
            content_size: data.content_size,
        },
        status: data.is_error ? "error" : "complete",
    });
    break;
}
```

### 4.3 Status/Log Events → `addLog()`

```typescript
case "status": {
    if (data.is_log) {
        store.addLog({
            message: data.status,
            level: data.level ?? "INFO",
            prefix: data.prefix ?? "",
        });
    }
    break;
}
```

### 4.4 Iteration End → `addLog()` (Summary)

```typescript
case "iteration_end": {
    store.incrementIterations();
    store.addLog({
        message: `Iteration ${count} ${status} — ${toolCalls} tool calls, ${duration}s`,
        level: "INFO",
        prefix: "Iteration",
    });
    break;
}
```

---

## 5. Frontend Rendering: `CombinedActivityLog` Component

### 5.1 Data Merging

The component merges two data sources into a single chronological timeline:

```typescript
const items: ActivityItem[] = useMemo(() => {
    const logItems = logs.map(l => ({ ...l, type: 'log' }));
    const toolItems = toolCalls.map(t => ({ ...t, type: 'tool' }));
    return [...logItems, ...toolItems].sort((a, b) => a.timestamp - b.timestamp);
}, [logs, toolCalls]);
```

### 5.2 Item Types

| Type | Component | Visual Style |
|------|-----------|-------------|
| **Tool (running)** | `ToolRow` | Blue left-border, pulsing play icon, collapsible input/result |
| **Tool (complete)** | `ToolRow` | Green left-border, check icon, collapsible input/result |
| **Tool (error)** | `ToolRow` | Red left-border, X icon, error-styled result |
| **Log (INFO)** | `LogRow` | Gray left-border, monospace text, timestamp |
| **Log (ERROR)** | `LogRow` | Red left-border, red-highlighted text |

### 5.3 Expand Modes

The Activity Log supports three expand modes, toggled via the header button:

- **Collapsed**: All items show headers only, no expanded detail
- **Open**: Items can be individually expanded/collapsed
- **Expanded**: All items fully expanded (input, result, full log text visible)

### 5.4 Collapsible Data

Tool inputs and results are rendered with `CollapsibleData`:
- Shows a size indicator (e.g., "2.3 KB")
- Preview text when collapsed
- Full JSON or text content when expanded
- Error-styled (red) for error results

---

## 6. Event Source Coverage Map

| Source | Event Type | Prefix | Status |
|--------|-----------|--------|--------|
| Hook: PreToolUse | `TOOL_CALL` | — | Active |
| Hook: PostToolUse | `TOOL_RESULT` | — | Active |
| Hook: Tool blocking | `STATUS` | `Hook` | Active |
| Hook: Skill hints | `STATUS` | `Hook` | Active |
| Hook: Validation errors | `STATUS` | `Hook` | Active |
| Local Toolkit bridge | `STATUS` | `[Local Toolkit]` | Active |
| Research bridge | `STATUS` | `[Local Toolkit]` | Active |
| Python logging (httpx) | `STATUS` | logger name | Active (via LogBridgeHandler) |
| Stdout/stderr capture | `STATUS` | `Console`/`System` | Active (via StdoutInterceptor) |
| Iteration summaries | `ITERATION_END` | `Iteration` | Active |
| MCP server calls | `STATUS` | `[Local Toolkit]` | Partial (via StdoutToEventStream wrappers) |
| Composio API calls | `STATUS` | logger name | Partial (via LogBridgeHandler on composio logger) |

### Gaps for Future Improvement

- **MCP server communication**: Some in-process MCP calls may not emit status events if they don't use `StdoutToEventStream` wrappers. Audit and add wrappers where missing.
- **Composio API detail**: The `composio` Python logger can be attached to `LogBridgeHandler` for richer API call visibility.

---

## 7. File Reference

| File | Role |
|------|------|
| `src/universal_agent/hooks.py` | `emit_tool_call_event()`, `emit_tool_result_event()`, `emit_status_event()`, `StdoutToEventStream`, `AgentHookSet` |
| `src/universal_agent/utils/log_bridge.py` | `LogBridgeHandler`, `StdoutInterceptor` |
| `src/universal_agent/main.py` | `run_conversation()` — direct tool event emission, `setup_log_bridge()` |
| `src/universal_agent/tools/local_toolkit_bridge.py` | In-process tool wrappers with `StdoutToEventStream` |
| `src/universal_agent/tools/research_bridge.py` | Research pipeline wrappers with `StdoutToEventStream` |
| `src/universal_agent/agent_core.py` | `EventType` enum, `AgentEvent` dataclass |
| `src/universal_agent/gateway_server.py` | WebSocket streaming, `agent_event_to_wire()` |
| `web-ui/lib/store.ts` | `processWebSocketEvent()` — event routing to logs/toolCalls |
| `web-ui/types/agent.ts` | `ToolCall`, `WebSocketEvent`, `StatusEventData` types |
| `web-ui/components/CombinedActivityLog.tsx` | `CombinedActivityLog`, `ToolRow`, `LogRow`, `CollapsibleData` |

---

## 8. Future Enhancement: Chat Panel Activity Summaries

Once the Activity Log is fully comprehensive, a planned enhancement will selectively surface useful operational detail into the Chat Panel during long silent periods:

- Brief "working on X..." status messages when no agent speech has occurred for N seconds but tool activity is ongoing
- Lightweight status cards showing search queries or MCP results
- This is a UX improvement designed to keep the user engaged while heavy tool execution is in progress
- The Activity Log remains the source of truth; the chat summaries are friendly abstractions of it
