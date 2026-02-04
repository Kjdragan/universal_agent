# 048 - Gateway Ops Service Architecture

## Overview

The Gateway Server (`gateway_server.py`) acts as the external HTTP/WebSocket interface for the Universal Agent. Besides handling agent communication (WebSocket), it now exposes a robust **Ops API** for managing sessions, logs, and system health.

This architecture achieves parity with the legacy Clawdbot system while adopting a more structured, service-oriented approach.

## Core Components

### 1. `OpsService` (`ops_service.py`)

A standalone service layer that encapsulates all operational logic. It is decoupled from the HTTP transport, making it testable and reusable.

**Responsibilities:**

- **Session Management**: Listing, resolving paths, retrieving details (heartbeat, memory status).
- **Log Management**: Efficient tailing of `run.log` and `activity_journal.log` with cursor support and byte limits.
- **Maintenance**: Resetting sessions (archiving), compacting logs (truncation), and deletion.

### 2. `GatewayServer` (`gateway_server.py`)

A FastAPI application that routes HTTP requests to the `OpsService`. It handles:

- **Authentication**: Checks `UA_OPS_TOKEN` or allowlists via `_require_ops_auth`.
- **Routing**: Maps REST endpoints to service methods.
- **Error Handling**: Translates service results into HTTP 404/400/503 responses.

---

## Ops API Endpoints

The endpoints map to Clawdbot's "Ops" namespace functionality:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/ops/sessions` | GET | List all active and archived sessions. Supports status filtering. |
| `/api/v1/ops/sessions/{id}` | GET | Get detailed session info (status, heartbeat, files). |
| `/api/v1/ops/sessions/{id}/preview` | GET | **Tail Activity Journal**. Returns dynamic log slice for UI streaming. |
| `/api/v1/ops/sessions/{id}/reset` | POST | **Archive Session**. Moves logs/memory/artifacts to an archive folder. |
| `/api/v1/ops/sessions/{id}/compact` | POST | **Truncate Logs**. Keeps only the tail of logs to save space. |
| `/api/v1/ops/sessions/{id}` | DELETE | **Delete Session**. Permanently removes workspace (requires `confirm=true`). |
| `/api/v1/ops/logs/tail` | GET | **Tail Run Log**. Generic log reader with cursor/limit support. |

## Data Flow

```mermaid
graph TD
    Client[Web UI / Ops Tool] -->|HTTP Request| API[Gateway Server API]
    API -->|Auth Check| API
    API -->|Call| Service[OpsService]
    
    subgraph Operations Layer
        Service -->|Read/Write| FS[File System (Workspaces)]
        Service -->|Query| Mem[Gateway Memory (Active Sessions)]
    end
    
    FS -->|Logs/State| Service
    Mem -->|Status| Service
    Service -->|DTO| API
    API -->|JSON| Client
```

## Log Tailing Algorithm

The `read_log_slice` method (and `tail_file`) implements a precise tailing algorithm for real-time streaming:

1. **Cursor-based**: Clients send the last `cursor` (file size) they read.
2. **Byte Limits**: Cap return payload size (default 250KB) to prevent OOM.
3. **Resync**: Detects if file was truncated/rotated (cursor > size) and resets.
4. **Efficiency**: Uses `seek()` for random access; does not read full file into memory.

## Design Decisions

- **Unification**: All file-system heavy operations are centralized in `OpsService`.
- **Parity**: Matching Clawdbot's specific features (like `preview` vs `log tail`) eases migration for frontend tools.
- **Safety**: Destructive operations (`delete`, `reset`) are behind explicit API actions and require confirmation parameters.
