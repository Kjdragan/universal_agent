# Universal Agent UI - Architecture

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND (Next.js)                             │
│                         http://localhost:3000                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                        Zustand Store                             │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │   │
│  │  │   messages   │  │  toolCalls   │  │    workProducts      │ │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────┘ │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │   │
│  │  │   sessions   │  │ connection   │  │       metrics        │ │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                  │                                       │
│  ┌───────────────────────────────┼───────────────────────────────────┐ │
│  │                         WebSocket Manager                          │ │
│  │  - Connect/Disconnect/Reconnect                                     │ │
│  │  - Send queries/approvals/ping                                      │ │
│  │  - Event callback subscriptions                                     │ │
│  └───────────────────────────────┼───────────────────────────────────┘ │
│                                  │                                       │
│  ┌───────────────────────────────┼───────────────────────────────────┐ │
│  │                     Event Processors                               │ │
│  │  - processWebSocketEvent() → Updates Zustand store                 │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
├─────────────────────────────────────────────────────────────────────────┤
│                              WebSocket                                    │
│                     ws://localhost:8001/ws/agent                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│                           BACKEND (FastAPI)                               │
│                         http://localhost:8001                            │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      WebSocket Handler                            │   │
│  │  - Accept connections                                             │   │
│  │  - Parse client messages (query, approval, ping)                  │   │
│  │  - Stream agent events as JSON                                     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                  │                                       │
│  ┌───────────────────────────────┼───────────────────────────────────┐ │
│  │                       Agent Bridge                                 │ │
│  │  - create_session() / resume_session()                             │ │
│  │  - execute_query() → streams AgentEvents                           │ │
│  │  - get_current_workspace()                                         │ │
│  │  - list_sessions()                                                  │ │
│  └───────────────────────────────┼───────────────────────────────────┘ │
│                                  │                                       │
│  ┌───────────────────────────────┼───────────────────────────────────┐ │
│  │                      UniversalAgent                                │ │
│  │  (from agent_core.py)                                              │ │
│  │  - run_query() → AsyncGenerator[AgentEvent]                        │ │
│  │  - Emits: text, tool_call, tool_result, work_product, etc.        │ │
│  └───────────────────────────────┼───────────────────────────────────┘ │
│                                  │                                       │
│  ┌───────────────────────────────┼───────────────────────────────────┐ │
│  │                    Composio + MCP                                   │ │
│  │  - External tools (Gmail, Search, etc.)                            │ │
│  │  - Local tools (file I/O, research)                               │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Query Execution

```
User Input (ChatInput)
    │
    ▼
sendQuery(text) ─────────────────────────────────────┐
    │                                               │
    ▼                                               │
WebSocket Message: {"type": "query", "text": "..."} │
    │                                               │
    └───────────────────────┐                       │
                            │ WebSocket
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ Backend: agent_bridge.execute_query()                          │
│   1. agent.run_query(query)                                     │
│   2. For each AgentEvent:                                       │
│      - Convert to WebSocketEvent                                │
│      - Send via WebSocket                                      │
└─────────────────────────────────────────────────────────────────┘
                            │
                            │ AgentEvent Stream
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ WebSocket Event Types Sent to Frontend:                        │
│                                                                 │
│  "session_info" → Connection established                        │
│  "text" → Append to streaming message                           │
│  "tool_call" → Add tool call card                               │
│  "tool_result" → Update tool call with result                   │
│  "thinking" → Show internal reasoning                           │
│  "status" → Update connection status                            │
│  "work_product" → Add to work products list                     │
│  "query_complete" → Finalize message                            │
│  "approval" → Show approval modal                               │
│  "error" → Display error                                         │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
processWebSocketEvent(event)
    │
    ▼
Zustand Store Update
    │
    ▼
Component Re-render
```

---

## Data Flow: Approval Required

```
Agent determines approval needed (e.g., planning phase complete)
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Backend: Emit "approval" event                                  │
│   {                                                             │
│     "type": "approval",                                         │
│     "data": {                                                   │
│       "phase_id": "planning",                                   │
│       "phase_name": "Planning Phase",                           │
│       "phase_description": "Review the generated mission plan", │
│       "tasks": [...],                                           │
│       "requires_followup": false                                │
│     }                                                           │
│   }                                                             │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼ WebSocket
    │
┌─────────────────────────────────────────────────────────────────┐
│ Frontend: useApprovalModal() hook receives event                 │
│   - setPendingApproval(data)                                    │
│   - ApprovalModal renders                                       │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
User reviews plan and clicks "Approve"
    │
    ▼
sendApproval({ phase_id, approved: true })
    │
    ▼ WebSocket
    │
Backend resumes agent execution
```

---

## Frontend State Management (Zustand)

```typescript
// Global State Structure
interface AgentStore {
  // Connection
  connectionStatus: "disconnected" | "connecting" | "connected" | "processing" | "error"

  // Session
  currentSession: SessionInfo | null
  sessions: Session[]

  // Messages (chat history)
  messages: Message[]
  currentStreamingMessage: string  // Temp buffer during streaming

  // Tool Calls (terminal log)
  toolCalls: ToolCall[]

  // Work Products (HTML reports, files)
  workProducts: WorkProduct[]

  // Thinking (internal reasoning)
  currentThinking: string

  // Metrics
  tokenUsage: { input: number; output: number; total: number }
  startTime: number | null
  toolCallCount: number
  iterationCount: number

  // UI State
  viewMode: {
    main: "chat" | "monitor" | "split"
    showWorkProducts: boolean
    showActivity: boolean
  }

  // Error Handling
  lastError: string | null
}
```

---

## Backend Event Protocol

### Event Types (from `events.py`)

| Event Type | Direction | Data Structure |
|------------|-----------|----------------|
| `connected` | Server→Client | `session: SessionInfo` |
| `text` | Server→Client | `text: string` |
| `tool_call` | Server→Client | `name, id, input, time_offset` |
| `tool_result` | Server→Client | `tool_use_id, is_error, content_preview, content_size` |
| `thinking` | Server→Client | `thinking: string` |
| `status` | Server→Client | `status, iteration?, token_usage?` |
| `work_product` | Server→Client | `content_type, content, filename, path` |
| `approval` | Server→Client | `phase_id, phase_name, phase_description, tasks` |
| `query_complete` | Server→Client | `session_id` |
| `error` | Server→Client | `message, details?` |
| `query` | Client→Server | `text: string` |
| `approval` | Client→Server | `phase_id, approved, followup_input?` |
| `ping` | Client→Server | `{}` |
| `pong` | Server→Client | `{}` |

---

## Component Hierarchy

```
HomePage (app/page.tsx)
├── Header
│   ├── Logo + Version
│   └── ConnectionIndicator
├── Sidebar (Sessions)
│   └── Session List
├── Main (ChatInterface)
│   ├── MessageList
│   │   └── ChatMessage (per message)
│   └── ChatInput
├── Sidebar (Monitor)
│   ├── MetricsPanel
│   ├── ActivityFeed
│   ├── TerminalLog
│   │   └── ToolCallCard (per tool call)
│   └── WorkProductViewer
└── ApprovalModal (conditional)
```

---

## WebSocket Reconnection Strategy

```python
# From websocket.ts
class AgentWebSocket {
  reconnectAttempts: 0
  maxReconnectAttempts: 5
  reconnectDelay: 1000ms (exponential backoff x1.5)

  onWebSocketClose():
    if (!manualClose && reconnectAttempts < max) {
      scheduleReconnect()
    }
}
```

---

## File System Integration

```
AGENT_RUN_WORKSPACES/
├── session_20250121_143000_a1b2c3d4/
│   ├── trace.json           # Session trace (token usage, tool calls)
│   ├── mission.json         # Mission plan (if using URW)
│   ├── search_results/      # SERP results saved by observer
│   ├── work_products/       # HTML reports, generated files
│   ├── workbench_activity/  # Remote workbench logs
│   └── tasks/               # Task-specific data
└── session_20250121_144500_e5f6g7h8/
    └── ...
```

The frontend can browse these directories via:
- REST API: `/api/files?session_id=xxx&path=subdir`
- WebSocket: Events include `session_info` with workspace path
