# 021 - Current State: Unified Gateway + Web UI

> Shareable snapshot of where we currently stand for the **unified gateway** architecture (CLI + Web UI using the same gateway execution engine).

---

## Goal (What we are trying to achieve)

- **Single canonical execution engine** exposed via the **Gateway Server**.
- Both clients:
  - **CLI** (terminal)
  - **Web UI** (React)

should act as **clients of the same gateway**.

---

## Current Architecture

### Recommended / Target Mode: Unified Gateway

- **Gateway Server** (port **8002**) runs the canonical execution engine.
- **Web UI API Server** (port **8001**) acts as a **thin proxy** to the gateway.
- **React Web UI** (port **3000**) connects to the API server (8001) over HTTP/WebSocket.
- **CLI** can connect directly to the gateway (8002) as a client.

High level flow:

- Web UI:
  - Browser (3000) -> API Server (8001) -> Gateway (8002)
- CLI:
  - CLI -> Gateway (8002)

---

## What Changed (Implementation Summary)

### Web UI now supports Gateway Mode

- The Web UI backend `universal_agent.api.server` no longer has to execute the agent in-process.
- When `UA_GATEWAY_URL` is set, the API server uses a gateway client bridge.

Key behavior:

- If `UA_GATEWAY_URL` is set:
  - API server uses `GatewayBridge` (proxy to gateway)
- If `UA_GATEWAY_URL` is NOT set:
  - API server uses `AgentBridge` (legacy in-process execution)

---

## Key Files

- `src/universal_agent/gateway_server.py`
  - External gateway server (HTTP + WebSocket).

- `src/universal_agent/api/server.py`
  - Web UI backend API server (HTTP + WebSocket endpoint `/ws/agent`).

- `src/universal_agent/api/agent_bridge.py`
  - **Selector**: chooses gateway bridge vs in-process bridge based on `UA_GATEWAY_URL`.

- `src/universal_agent/api/gateway_bridge.py`
  - Gateway client used by the API server:
    - Creates sessions via `POST /api/v1/sessions`
    - Streams events via `ws://.../api/v1/sessions/{id}/stream`

- `start_gateway.sh`
  - Unified startup entrypoint for the gateway + web UI stack.

- `Project_Documentation/020_Startup_Scripts_Guide.md`
  - Updated to recommend `./start_gateway.sh` as the primary way to run.

---

## How to Run (Unified Gateway Mode)

### Start the unified stack (Gateway + API + Web UI)

```bash
./start_gateway.sh
```

Services:

- Gateway: http://localhost:8002
- API: http://localhost:8001
- Web UI: http://localhost:3000

### Run the CLI against the same gateway (separate terminal)

```bash
UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh
```

---

## What We Have Verified So Far

- Gateway server starts and responds to health:
  - `GET http://localhost:8002/api/v1/health`

- Web UI API server (8001) can use `UA_GATEWAY_URL` to create sessions on the gateway:
  - API server logs show it calling:
    - `POST http://localhost:8002/api/v1/sessions`

This confirms the Web UI API server is acting as a gateway client at least for session creation.

---

## What Is NOT Yet Fully Verified (Remaining Work)

### 1) CLI “gateway client mode” golden-run equivalent

We still need to do a clean end-to-end run where:

- Gateway server is running
- CLI is configured as a **gateway client** (via `UA_GATEWAY_URL`)
- A representative prompt is executed successfully

Primary risk areas to validate:

- WebSocket streaming behavior
- Session persistence
- Any SQLite locking / shared runtime state issues

### 2) Web UI full end-to-end against gateway

We still need to validate the full path:

- Browser UI sends query -> API server -> gateway
- Streaming responses/events appear correctly in the UI
- Approvals/tool-calls (if applicable) behave correctly

---

## Known “Cleanliness” Notes

- The repo may generate artifacts during local runs (not intended for commit):
  - `src/universal_agent.egg-info/`
  - `scripts/test_results.json`

These are now ignored via `.gitignore`.

---

## Next Actions (Recommended)

1. Start unified stack:
   - `./start_gateway.sh`

2. Run CLI in gateway-client mode:
   - `UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh`

3. Open the Web UI and run a query:
   - http://localhost:3000

4. If anything fails, capture:
   - `gateway.log`
   - `api.log`
   - browser console output

---

*Last updated: 2026-01-27*
