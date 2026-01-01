# 005 - Event Types and Message Protocol

This document explains the message protocol between the Python backend and JavaScript frontend.

---

## The Two-Way Contract

The frontend and backend communicate via JSON messages over WebSocket.

### Frontend → Backend (User Actions)

| Type | When Sent | Data |
|------|-----------|------|
| `query` | User sends a message | `{ type: "query", text: "..." }` |
| `ping` | Keep-alive check | `{ type: "ping" }` |

### Backend → Frontend (Agent Events)

| Type | When Sent | Purpose |
|------|-----------|---------|
| `session_info` | On connect | Session metadata |
| `text` | Agent speaking | Streaming response text |
| `tool_call` | Agent using tool | Show tool activity |
| `tool_result` | Tool finished | Show result preview |
| `thinking` | Extended thinking | Agent reasoning |
| `auth_required` | OAuth needed | Show auth link |
| `query_complete` | Done with query | Reset UI state |
| `work_product` | Report saved | Load into Output panel |
| `error` | Something failed | Show error message |

---

## Python Side: Emitting Events

In `agent_core.py`, the `UniversalAgent` class yields `AgentEvent` objects:

```python
from enum import Enum
from dataclasses import dataclass

class EventType(str, Enum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    WORK_PRODUCT = "work_product"
    # ... etc

@dataclass
class AgentEvent:
    type: EventType
    data: dict
    timestamp: float
```

The `run_query()` method is an **async generator** that yields events:

```python
async def run_query(self, query: str) -> AsyncGenerator[AgentEvent, None]:
    # ... processing ...
    
    yield AgentEvent(
        type=EventType.TEXT,
        data={"text": "Hello, how can I help?"}
    )
    
    yield AgentEvent(
        type=EventType.TOOL_CALL,
        data={"name": "SERPAPI_SEARCH", "id": "abc123"}
    )
```

---

## Python Side: Sending to WebSocket

In `server.py`, the FastAPI WebSocket handler converts events to JSON:

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    agent = UniversalAgent()
    
    while True:
        # Wait for client message
        data = await websocket.receive_text()
        message = json.loads(data)
        
        if message.get("type") == "query":
            query = message.get("text", "")
            
            # Stream events back to client
            async for event in agent.run_query(query):
                await websocket.send_json({
                    "type": event.type.value,  # "text", "tool_call", etc.
                    "data": event.data,
                    "timestamp": event.timestamp
                })
```

---

## JavaScript Side: Receiving Events

```javascript
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    handleServerMessage(msg);
};

function handleServerMessage(msg) {
    switch (msg.type) {
        case 'text':
            // msg.data.text contains the text
            break;
        case 'tool_call':
            // msg.data.name, msg.data.id
            break;
        // ...
    }
}
```

---

## Event Details

### `session_info`
Sent immediately after WebSocket connects:
```json
{
    "type": "session_info",
    "data": {
        "workspace": "AGENT_RUN_WORKSPACES/session_20251223_001234",
        "user_id": "user_123",
        "session_url": "https://mcp.composio.dev/..."
    }
}
```

### `text`
Sent as the agent generates response (streaming):
```json
{
    "type": "text",
    "data": {
        "text": "I'll search for that information"
    }
}
```
Multiple `text` events combine to form the full response.

### `tool_call`
Sent when agent invokes a tool:
```json
{
    "type": "tool_call",
    "data": {
        "name": "SERPAPI_SEARCH_NEWS",
        "id": "toolu_abc123",
        "input": {"query": "AI news"},
        "time_offset": 2.5
    }
}
```

### `tool_result`
Sent when tool returns:
```json
{
    "type": "tool_result",
    "data": {
        "tool_use_id": "toolu_abc123",
        "is_error": false,
        "content_preview": "{\"news_results\": [...]}",
        "content_size": 4523
    }
}
```

### `work_product`
Sent when HTML report is saved:
```json
{
    "type": "work_product",
    "data": {
        "content_type": "text/html",
        "content": "<!DOCTYPE html>...",
        "filename": "ai_report.html",
        "path": "AGENT_RUN.../work_products/ai_report.html"
    }
}
```

### `query_complete`
Sent when agent finishes responding:
```json
{
    "type": "query_complete",
    "data": {}
}
```

---

## The Flow for a Simple Query

```
User types "Hello"
    │
    ▼
JS sends: {"type": "query", "text": "Hello"}
    │
    ▼
Python receives, calls agent.run_query("Hello")
    │
    ├─▶ yields AgentEvent(TEXT, {"text": "Hello! "})
    │       └─▶ JS appends "Hello! " to message bubble
    │
    ├─▶ yields AgentEvent(TEXT, {"text": "How can I help?"})
    │       └─▶ JS appends "How can I help?"
    │
    └─▶ yields AgentEvent(ITERATION_END, {...})
            └─▶ Python sends query_complete
                    └─▶ JS marks message as complete
```

---

## The Flow for a Tool-Using Query

```
User types "Search for AI news"
    │
    ▼
JS sends: {"type": "query", "text": "Search for AI news"}
    │
    ▼
Python: agent.run_query("Search for AI news")
    │
    ├─▶ TEXT: "I'll search for that..."
    │
    ├─▶ TOOL_CALL: {name: "SERPAPI_SEARCH_NEWS", ...}
    │       └─▶ JS shows "Calling SERPAPI_SEARCH_NEWS..."
    │
    ├─▶ TOOL_RESULT: {content_preview: "...", ...}
    │       └─▶ JS shows tool result in context panel
    │
    ├─▶ TEXT: "I found several articles..."
    │
    └─▶ WORK_PRODUCT: {content_type: "text/html", content: "..."}
            └─▶ JS loads HTML into output iframe
                └─▶ JS switches to Output view
```

---

## Error Handling

```json
{
    "type": "error",
    "data": {
        "message": "Connection to MCP server failed"
    }
}
```

JS displays this in the context panel with red/amber styling.

---

## Summary

The protocol is simple:
1. **Client sends `query`** with user text
2. **Server streams events** as agent works
3. **Client updates UI** based on event type
4. **Server sends `query_complete`** when done

All messages are JSON. The `type` field determines how to handle each message.
