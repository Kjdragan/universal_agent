# Gateway Ops API

The **Ops API** provides internal endpoints for managing the agent's lifecycle, session history, and system logs. It is primarily used by the Web UI and administrative tools.

## 1. Overview

The operations layer is separated into a dedicated `OpsService` to ensure the core agent logic remains decoupled from the HTTP transport and transport-specific logic.

```mermaid
graph LR
    Client[Web UI] --> API[FastAPI Endpoints]
    API --> Service[OpsService]
    Service --> Disk[Workspaces & Logs]
```

## 2. Ops API Endpoints

All endpoints are prefixed with `/api/v1/ops/` and require the `UA_OPS_TOKEN` if authentication is enabled.

### Session Management

- **`GET /sessions`**: List all active and archived sessions.
- **`GET /sessions/{id}`**: Get detailed session metadata (path, last run, status).
- **`DELETE /sessions/{id}`**: Permanently remove a session workspace.

### Log & Activity Monitoring

- **`GET /sessions/{id}/preview`**: Tails the **Activity Journal** (high-level events) for UI streaming.
- **`GET /logs/tail`**: Tails the **Run Log** (raw console output) with support for byte offsets and cursors.

### Maintenance

- **`POST /sessions/{id}/reset`**: Archives the current session into a timestamped subfolder and prepares for a fresh run.
- **`POST /sessions/{id}/compact`**: Truncates large logs to save disk space while preserving the most recent entries.

## 3. The Tailing Algorithm

To support high-frequency log updates in the Web UI, the API uses a seek-based tailing algorithm:

1. **Cursor Tracking**: The frontend sends the last `cursor` (byte offset) it received.
2. **Byte Windows**: The backend only reads and returns the *new* data since that cursor.
3. **Resync Logic**: If a log is rotated or truncated, the backend detects the cursor mismatch and resets to the beginning of the current file.

## 4. Files

- **Service Implementation**: `src/universal_agent/ops_service.py`
- **Routing**: `src/universal_agent/gateway_server.py`
