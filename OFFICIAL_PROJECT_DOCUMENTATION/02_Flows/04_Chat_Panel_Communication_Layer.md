# 04 Chat Panel Communication Layer

This document describes the complete data pipeline that populates the **Chat Panel** — the primary user-facing dialogue window in the Web UI. It covers every layer from SDK message receipt through backend event emission, WebSocket transport, frontend store processing, and final React rendering.

---

## 1. Purpose & Design Principles

The Chat Panel displays **agent dialogue** — what the agent (and its sub-agents) "say" and "think" in conversation with the user.

### What Belongs Here

| Content Type | Source Event | Example |
|-------------|-------------|---------|
| **Agent speech** | `TEXT` event | "I've completed the research and found 3 key sources..." |
| **Sub-agent speech** | `TEXT` event (attributed) | Research Specialist: "Searching for quantum computing papers..." |
| **Thinking blocks** | `THINKING` event | Extended reasoning before a decision |
| **User messages** | Local (user input) | "Research the latest AI developments" |

### What Does NOT Belong Here

- Raw tool calls / tool results (→ Activity Log)
- Status/log messages (→ Activity Log)
- Hook debug info, schema validation detail (→ Activity Log)
- MCP server communication detail (→ Activity Log)

### Principle: No Duplication, No Fragmentation

Each piece of agent communication appears **once** in the chat panel, rendered as a **single coherent bubble** per agent turn. We err on the side of more information rather than less, but never duplicate content within the same panel.

---

## 2. Backend Event Pipeline

### 2.1 Source: Claude Agent SDK

The Claude Agent SDK delivers messages as typed objects during `client.receive_response()`:

- **`AssistantMessage`** — contains `content` blocks: `TextBlock`, `ToolUseBlock`, `ThinkingBlock`
- **`ResultMessage`** — end-of-turn summary with token usage
- **`UserMessage` / `ToolResultBlock`** — tool result delivery

Only `TextBlock` and `ThinkingBlock` produce chat-bound events. `ToolUseBlock` and `ToolResultBlock` are routed exclusively to the Activity Log.

### 2.2 Event Emission: `run_conversation()` in `main.py`

During the streaming loop in `run_conversation()`, each SDK message is processed:

```
async for msg in client.receive_response():
    if isinstance(msg, AssistantMessage):
        # Resolve author from parent_tool_use_id (sub-agent tracking)
        _current_author = resolve_author(msg, _tool_name_map)

        for block in msg.content:
            if isinstance(block, TextBlock):
                hook_events.emit_text_event(block.text, author=_current_author)

            elif isinstance(block, ThinkingBlock):
                hook_events.emit_thinking_event(
                    block.thinking, block.signature, author=_current_author
                )

            elif isinstance(block, ToolUseBlock):
                # → Activity Log only (via hook_events.emit_tool_call_event)
                ...
```

### 2.3 Author Attribution (Sub-Agent Tracking)

The system tracks which agent is "speaking" using two mechanisms:

1. **`_tool_name_map`** — maps `tool_use_id` → resolved agent name. Populated when `ToolUseBlock` is seen:
   - `Task` tool with `subagent_type: "research-specialist"` → `"Subagent: research-specialist"`
   - Tool name containing "research" → `"Research Specialist"`
   - Tool name containing "report"/"compile" → `"Report Writer"`

2. **`parent_tool_use_id`** on `AssistantMessage` — when present, indicates the message is from a sub-agent. The ID is looked up in `_tool_name_map` to resolve the author name.

When no `parent_tool_use_id` is present, the author defaults to `"Primary Agent"`.

### 2.4 Event Functions: `hooks.py`

```python
def emit_text_event(text: str, author: Optional[str] = None) -> None:
    _emit_event(AgentEvent(
        type=EventType.TEXT,
        data={
            "text": text,
            "author": author or "Primary Agent",
            "time_offset": _tool_time_offset(),
        },
    ))

def emit_thinking_event(thinking: str, signature=None, author=None) -> None:
    _emit_event(AgentEvent(
        type=EventType.THINKING,
        data={
            "thinking": thinking,
            "signature": signature,
            "author": author or "Primary Agent",
            "time_offset": _tool_time_offset(),
        },
    ))
```

These functions use `ContextVar`-based callbacks set by the gateway at the start of each execution, ensuring no cross-session contamination.

### 2.5 Deduplication: Eliminated Triple Emission

Previously, the same final agent text was emitted **3 times**:

| # | Location | When | Status |
|---|----------|------|--------|
| 1 | `hooks.py:emit_text_event()` | Real-time during `run_conversation` | **Kept** (primary source) |
| 2 | `process_turn()` post-completion | After `run_conversation` returns | **Removed** |
| 3 | `execution_engine.py` `final: True` | After `process_turn` returns | **Filtered** by gateway safety net |

Now only emission #1 (the real-time streaming path) populates the chat. The `print()` statement for CLI output remains for terminal users.

---

## 3. Transport: WebSocket

Events flow from the backend to the frontend via WebSocket:

```
hook_events.emit_text_event()
  → _emit_event() → ContextVar callback
    → event_queue (asyncio.Queue)
      → gateway.execute() async generator
        → gateway_server.websocket_stream()
          → WebSocket JSON: {"type": "text", "data": {...}, "timestamp": "..."}
```

The gateway server applies a safety-net dedup filter: if `final: True` text is seen after streaming text was already sent, it's dropped (`gateway_server.py:1398-1404`).

---

## 4. Frontend Store: Stream Coalescing (`store.ts`)

### 4.1 Zustand State

```typescript
currentStreamingMessage: string;   // Accumulating text buffer
currentAuthor?: string;            // Who is currently "speaking"
currentOffset?: number;            // Time offset for the current stream
```

### 4.2 `appendToStream()` — Author-Aware Coalescing

When a `text` event arrives:

1. If the **author changed** and there's buffered text → **auto-finalize** the current stream as a completed `Message`, then start a new stream with the new author.
2. If the **author is the same** → simply append the text to the buffer.

This means consecutive text from the same agent merges into one message, while a switch between Primary Agent and Research Specialist automatically creates separate attributed messages.

### 4.3 What Triggers `finishStream()`

| Trigger | Behavior |
|---------|----------|
| Author change in `appendToStream()` | Auto-finalize old stream, start new |
| `query_complete` event | Finalize any remaining stream |
| ~~`tool_call` event~~ | **No longer triggers finishStream** — tool calls only go to Activity Log |

Previously, every `tool_call` event caused `finishStream()`, which fragmented agent speech into many small bubbles. This was removed — tool calls are Activity Log-only events.

### 4.4 Message Type

The `Message` interface includes:

```typescript
interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  author?: string;           // "Primary Agent", "Research Specialist", etc.
  messageType?: "speech" | "thought";
  thinking?: string;         // Attached thinking block content
  time_offset: number;
  is_complete: boolean;
}
```

---

## 5. Frontend Rendering: `ChatMessage` Component (`page.tsx`)

### 5.1 Agent Style Resolution

A shared `getAgentStyle(author)` function maps author names to visual properties:

| Author Pattern | Icon | Color | Border Accent |
|---------------|------|-------|---------------|
| Primary Agent | 🤖 | Blue | `border-l-blue-500/40` |
| Research Specialist | 🔍 | Purple | `border-l-purple-500/40` |
| Report Writer | 📝 | Orange | `border-l-orange-500/40` |
| Planner/Orchestrator | 🗺️ | Cyan | `border-l-cyan-500/40` |
| Verifier/Tester | ✅ | Green | `border-l-green-500/40` |
| Image/Video Agent | 🎨 | Pink | `border-l-pink-500/40` |
| Other Subagent | ⚙️ | Emerald | `border-l-emerald-500/40` |

### 5.2 Message Rendering

Each finalized message is rendered as **one consolidated bubble**:

- **User messages**: right-aligned, primary color accent
- **Assistant messages**: left-aligned with:
  - Agent avatar (icon in colored circle)
  - Author label (uppercase, colored)
  - Delta timestamp
  - Left-border color accent by agent type
  - Full markdown rendering (ReactMarkdown + remarkGfm)
  - Attached thinking block (collapsible, amber-styled) when `message.thinking` is present

The previous behavior of splitting message content on `\n\n` into separate bubbles has been removed. One message = one bubble.

### 5.3 Streaming View

The streaming message view (visible while text is being received) uses the same `getAgentStyle()` utility for visual consistency with finalized messages, plus a pulsing cursor indicator.

---

## 6. File Reference

| File | Role |
|------|------|
| `src/universal_agent/main.py` | `run_conversation()` — SDK message processing, author tracking, event emission |
| `src/universal_agent/hooks.py` | `emit_text_event()`, `emit_thinking_event()` — event creation with author |
| `src/universal_agent/agent_core.py` | `EventType` enum, `AgentEvent` dataclass |
| `src/universal_agent/gateway_server.py` | WebSocket streaming, dedup safety net |
| `src/universal_agent/gateway.py` | `InProcessGateway.execute()` — event generator |
| `src/universal_agent/execution_engine.py` | `ProcessTurnAdapter` — bridges `process_turn` to event stream |
| `web-ui/lib/store.ts` | Zustand store — `appendToStream()`, `finishStream()`, `processWebSocketEvent()` |
| `web-ui/types/agent.ts` | `Message`, `MessageType`, `WebSocketEvent` types |
| `web-ui/app/page.tsx` | `ChatMessage`, `ChatInterface`, `getAgentStyle()`, `ThinkingBubble` |

---

## 7. Future Enhancement: Activity Summaries in Chat (Phase 7)

During long tool execution periods where the agent isn't communicating, the chat panel can feel silent while the Activity Log is busy. A planned future enhancement will surface friendly status summaries from the activity log into the chat panel:

- Brief "working on X..." messages when no TEXT events have been emitted for N seconds
- Lightweight status cards showing search queries or MCP results
- Designed to be implemented after the activity log is fully comprehensive
