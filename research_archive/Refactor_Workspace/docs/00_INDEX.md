# UA Gateway Refactor — Documentation Index

**Version:** 1.0  
**Completed:** 2026-01-24  
**Status:** All 6 Stages Complete ✅

---

## Document Map

| # | Document | Description |
|---|----------|-------------|
| 00 | [INDEX.md](00_INDEX.md) | This file - documentation overview |
| 01 | [ARCHITECTURE_OVERVIEW.md](01_ARCHITECTURE_OVERVIEW.md) | High-level architecture and design principles |
| 02 | [GATEWAY_API.md](02_GATEWAY_API.md) | Gateway interface, sessions, and request/response flow |
| 03 | [EVENT_STREAM.md](03_EVENT_STREAM.md) | Event types, streaming protocol, and rendering |
| 04 | [EXTERNAL_GATEWAY.md](04_EXTERNAL_GATEWAY.md) | HTTP/WebSocket server and client implementation |
| 05 | [URW_INTEGRATION.md](05_URW_INTEGRATION.md) | Universal Ralph Wrapper gateway adapter |
| 06 | [WORKER_POOL.md](06_WORKER_POOL.md) | Distributed execution with lease durability |
| 07 | [SEQUENCE_DIAGRAMS.md](07_SEQUENCE_DIAGRAMS.md) | Mermaid sequence diagrams for all flows |
| 08 | [TESTING_GUIDE.md](08_TESTING_GUIDE.md) | Test organization and execution guide |

## Additional Living Docs

- **CLI-centric execution engine refactor**: `../cli_centric_gateway_refactor/00_INDEX.md`

---

## Quick Start

### Using the Gateway (CLI)

```bash
# In-process gateway (default in dev mode)
UA_USE_GATEWAY=1 python -m universal_agent "Hello"

# External gateway server
python -m universal_agent.gateway_server  # Start server on :8002
python -m universal_agent --gateway-url http://localhost:8002 "Hello"
```

### Using the Gateway (Python)

```python
from universal_agent.gateway import InProcessGateway, GatewayRequest

gateway = InProcessGateway()
session = await gateway.create_session(user_id="user1", workspace_dir="/tmp/ws")
request = GatewayRequest(user_input="List files in current directory")

async for event in gateway.execute(session, request):
    print(f"{event.type}: {event.data}")
```

### Worker Pool (Distributed)

```python
from universal_agent.durable import WorkerPoolManager, PoolConfig, queue_run

pool = WorkerPoolManager(PoolConfig(min_workers=2, max_workers=8))
await pool.start()

# Queue work
queue_run(conn, run_id="job_1", prompt="Process data", workspace_dir="/data")
```

---

## Implementation Summary

### Stage 1: Dependency Hardening
- Isolated gateway dependencies from core agent
- Added graceful degradation for optional features

### Stage 2: Event Stream Normalization
- Unified `EventType` enum for all agent events
- Parity between CLI and gateway output rendering

### Stage 3: Gateway API In-Process
- `InProcessGateway` class with session management
- `GatewayRequest`/event streaming interface

### Stage 4: Gateway Externalization
- FastAPI server (`gateway_server.py`) with REST + WebSocket
- `ExternalGateway` client for remote connections

### Stage 5: URW Integration
- `GatewayURWAdapter` for harness execution through gateway
- URW phase events (`URW_PHASE_START`, `URW_PHASE_COMPLETE`, etc.)

### Stage 6: Worker Pool + Lease Durability
- `WorkerPoolManager` with dynamic scaling
- Lease-based coordination for distributed execution

---

## Key Files

| File | Purpose |
|------|---------|
| `src/universal_agent/gateway.py` | Gateway interface + InProcess/External implementations |
| `src/universal_agent/gateway_server.py` | FastAPI external gateway server |
| `src/universal_agent/agent_core.py` | EventType enum, UniversalAgent class |
| `src/universal_agent/urw/integration.py` | GatewayURWAdapter |
| `src/universal_agent/urw/harness_orchestrator.py` | HarnessOrchestrator with gateway mode |
| `src/universal_agent/durable/worker_pool.py` | Worker pool for distributed execution |
| `src/universal_agent/durable/state.py` | Lease acquisition/heartbeat functions |
