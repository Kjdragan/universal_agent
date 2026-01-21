# Universal Agent UI - API Reference

## WebSocket API

### Connection

**Endpoint:** `ws://localhost:8001/ws/agent`

**Connection Flow:**
1. Client connects to WebSocket
2. Server immediately sends `connected` event with session info
3. Client can then send queries and receive real-time events

### Client → Server Messages

#### Query
Send a user query to the agent.

```json
{
  "type": "query",
  "data": {
    "text": "Research the latest developments in quantum computing"
  },
  "timestamp": 1737529200000
}
```

#### Approval
Approve or reject a URW phase (planning, replan, etc.).

```json
{
  "type": "approval",
  "data": {
    "phase_id": "planning",
    "approved": true,
    "followup_input": "Please focus on quantum error correction"
  },
  "timestamp": 1737529200000
}
```

#### Ping
Keep-alive ping (server responds with `pong`).

```json
{
  "type": "ping",
  "data": {},
  "timestamp": 1737529200000
}
```

---

### Server → Client Events

#### connected
Sent immediately after WebSocket connection is established.

```json
{
  "type": "connected",
  "data": {
    "message": "Connected to Universal Agent",
    "session": {
      "session_id": "session_20250121_143000_a1b2c3d4",
      "workspace": "/path/to/AGENT_RUN_WORKSPACES/session_20250121_143000_a1b2c3d4",
      "user_id": "user_ui",
      "session_url": "https://composio.dev/session/xxx",
      "logfire_enabled": true
    }
  },
  "timestamp": 1737529200000
}
```

#### text
Streaming text response from the agent. Multiple `text` events may be sent for a single response.

```json
{
  "type": "text",
  "data": {
    "text": "I'll research the latest quantum computing developments for you."
  },
  "timestamp": 1737529201000
}
```

#### tool_call
Sent when the agent calls a tool.

```json
{
  "type": "tool_call",
  "data": {
    "name": "mcp__composio__COMPOSIO_SEARCH_WEB",
    "id": "toolu_01abc123",
    "input": {
      "tool_slug": "SEARCH_WEB",
      "input": {
        "query": "quantum computing 2025 breakthrough",
        "num_results": 10
      }
    },
    "time_offset": 2.345
  },
  "timestamp": 1737529202000
}
```

#### tool_result
Sent when a tool call completes.

```json
{
  "type": "tool_result",
  "data": {
    "tool_use_id": "toolu_01abc123",
    "is_error": false,
    "content_preview": "Found 10 search results about quantum computing...",
    "content_size": 5432
  },
  "timestamp": 1737529205000
}
```

#### thinking
Agent's internal reasoning (optional, not always sent).

```json
{
  "type": "thinking",
  "data": {
    "thinking": "User wants research on quantum computing. I should search for recent papers and news."
  },
  "timestamp": 1737529200000
}
```

#### status
Status update during execution.

```json
{
  "type": "status",
  "data": {
    "status": "processing",
    "iteration": 1,
    "tokens": {
      "input": 1500,
      "output": 300,
      "total": 1800
    }
  },
  "timestamp": 1737529200000
}
```

#### work_product
A work product (HTML report, file) was created.

```json
{
  "type": "work_product",
  "data": {
    "content_type": "text/html",
    "content": "<!DOCTYPE html>...",
    "filename": "quantum_computing_report.html",
    "path": "/path/to/workspace/work_products/quantum_computing_report.html"
  },
  "timestamp": 1737529250000
}
```

#### approval
Agent requires user approval (planning phase, replan request, etc.).

```json
{
  "type": "approval",
  "data": {
    "phase_id": "planning",
    "phase_name": "Planning Phase",
    "phase_description": "Please review the generated mission plan before execution begins.",
    "tasks": [
      {
        "id": "task_1",
        "content": "Search for recent quantum computing breakthroughs",
        "activeForm": "Searching for quantum computing news",
        "status": "pending"
      },
      {
        "id": "task_2",
        "content": "Crawl and analyze research papers",
        "activeForm": "Analyzing papers",
        "status": "pending"
      }
    ],
    "requires_followup": false
  },
  "timestamp": 1737529200000
}
```

#### query_complete
Query execution finished (final event for a query).

```json
{
  "type": "query_complete",
  "data": {
    "session_id": "session_20250121_143000_a1b2c3d4"
  },
  "timestamp": 1737529300000
}
```

#### error
An error occurred.

```json
{
  "type": "error",
  "data": {
    "message": "Failed to connect to Composio API",
    "details": {
      "error_code": "COMPOSIO_CONNECTION_ERROR",
      "retryable": true
    }
  },
  "timestamp": 1737529200000
}
```

---

## REST API

### Base URL
`http://localhost:8001`

### Endpoints

#### GET /
Root endpoint - API information.

```bash
curl http://localhost:8001/
```

**Response:**
```json
{
  "name": "Universal Agent API",
  "version": "2.0.0",
  "status": "running",
  "endpoints": {
    "websocket": "/ws/agent",
    "sessions": "/api/sessions",
    "files": "/api/files",
    "health": "/api/health"
  }
}
```

---

#### GET /api/health
Health check.

```bash
curl http://localhost:8001/api/health
```

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-01-21T14:30:00.000Z",
  "version": "2.0.0"
}
```

---

#### POST /api/sessions
Create a new agent session.

```bash
curl -X POST http://localhost:8001/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_ui"}'
```

**Request Body:**
```json
{
  "user_id": "user_ui"  // optional, defaults to "user_ui"
}
```

**Response:**
```json
{
  "session_id": "session_20250121_143000_a1b2c3d4",
  "workspace": "/path/to/AGENT_RUN_WORKSPACES/session_20250121_143000_a1b2c3d4",
  "user_id": "user_ui"
}
```

---

#### GET /api/sessions
List all agent sessions.

```bash
curl http://localhost:8001/api/sessions
```

**Response:**
```json
{
  "sessions": [
    {
      "session_id": "session_20250121_143000_a1b2c3d4",
      "timestamp": 1737529200,
      "workspace_path": "/path/to/session_20250121_143000_a1b2c3d4",
      "status": "complete",
      "files": {
        "work_products": [
          {"name": "report.html", "path": "work_products/report.html", "size": 12345}
        ],
        "search_results": [],
        "workbench_activity": [],
        "other": []
      }
    }
  ]
}
```

---

#### GET /api/sessions/{session_id}
Get session details.

```bash
curl http://localhost:8001/api/sessions/session_20250121_143000_a1b2c3d4
```

**Response:**
```json
{
  "session_id": "session_20250121_143000_a1b2c3d4",
  "workspace": "/path/to/session_20250121_143000_a1b2c3d4",
  "trace": {
    "run_id": "uuid",
    "query": "Research quantum computing",
    "start_time": "2025-01-21T14:30:00Z",
    "end_time": "2025-01-21T14:32:30Z",
    "total_duration_seconds": 150.5,
    "tool_calls": [...],
    "token_usage": {"input": 5000, "output": 1000, "total": 6000}
  }
}
```

---

#### GET /api/files
List files in a session workspace.

```bash
curl "http://localhost:8001/api/files?session_id=session_xxx&path=work_products"
```

**Query Parameters:**
- `session_id` (optional): Session ID, defaults to current
- `path` (optional): Subdirectory path, defaults to root

**Response:**
```json
{
  "files": [
    {
      "name": "report.html",
      "path": "work_products/report.html",
      "is_dir": false,
      "size": 12345,
      "modified": 1737529300
    }
  ],
  "path": "work_products",
  "workspace": "/path/to/workspace"
}
```

---

#### GET /api/files/{session_id}/{file_path}
Get file content from session workspace.

```bash
curl http://localhost:8001/api/files/session_xxx/work_products/report.html
```

**Response:**
- HTML files: Returns HTML content
- JSON files: Returns parsed JSON
- Text files: Returns plain text
- Other files: Returns file download

---

#### POST /api/approvals
Submit approval for URW phase.

```bash
curl -X POST http://localhost:8001/api/approvals \
  -H "Content-Type: application/json" \
  -d '{
    "phase_id": "planning",
    "approved": true,
    "followup_input": "Focus on quantum error correction"
  }'
```

**Request Body:**
```json
{
  "phase_id": "planning",
  "approved": true,
  "followup_input": "Optional additional context"
}
```

**Response:**
```json
{
  "status": "approved",
  "phase_id": "planning"
}
```

---

## TypeScript Types

```typescript
// From types/agent.ts

type EventType =
  | "text" | "tool_call" | "tool_result" | "thinking"
  | "status" | "auth_required" | "error" | "session_info"
  | "iteration_end" | "work_product" | "connected"
  | "query_complete" | "pong" | "query" | "approval" | "ping";

interface WebSocketEvent {
  type: EventType;
  data: EventData;
  timestamp: number;
}

interface SessionInfo {
  session_id: string;
  workspace: string;
  user_id: string;
  session_url?: string;
  logfire_enabled?: boolean;
}

interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  time_offset: number;
  result?: ToolResult;
  status: "pending" | "running" | "complete" | "error";
}

interface WorkProduct {
  id: string;
  content_type: string;
  content: string;
  filename: string;
  path: string;
  timestamp: number;
}

type ConnectionStatus =
  | "disconnected" | "connecting" | "connected"
  | "processing" | "error";
```

---

## Error Codes

| Error | Description | Retryable |
|-------|-------------|-----------|
| `WEBSOCKET_CONNECTION_FAILED` | Cannot connect to backend | Yes |
| `SESSION_NOT_FOUND` | Session ID doesn't exist | No |
| `FILE_NOT_FOUND` | File doesn't exist in workspace | No |
| `INVALID_PATH` | Path traversal attempt | No |
| `AGENT_EXECUTION_FAILED` | Agent crashed during execution | Yes |
| `COMPOSIO_AUTH_REQUIRED` | Composio API needs auth | No (user action) |
| `CONTEXT_EXHAUSTED` | Agent ran out of token context | Yes (agent auto-recovers) |
