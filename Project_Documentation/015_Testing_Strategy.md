# Testing Strategy & Documentation

This document outlines the testing framework for the Universal Agent project.

## 1. Test Suite Overview

We use `pytest` as our primary testing runner. The suite currently contains ~158 tests covering:

*   **Unit Tests** (`tests/unit`): Isolate checks for specific components (Agent, Gateway, etc).
*   **Integration Tests** (`tests/integration`, `tests/gateway`): Verify component interactions.
*   **Durable State Tests** (`tests/durable`): Verify persistence and recovery logic.
*   **Stabilization (Smoke) Tests** (`tests/stabilization`): End-to-End verification of critical paths.

## 2. Running the Tests

### Full Curated Suite (Standard)
To run the "Parity Suite" (excluding flaky integration tests and disabled modules):
```bash
PYTHONPATH=src uv run pytest tests/unit tests/durable tests/gateway tests/stabilization
```

### Smoke Tests (Fast)
To verify basic system health (Plumbing check, ~30s):
```bash
./run_verification.sh
```

### Specific Sub-Suites
```bash
# Gateway specific
PYTHONPATH=src uv run pytest tests/gateway

# Durable State specific
PYTHONPATH=src uv run pytest tests/durable
```

## 3. Key Test Directories

| Directory | Purpose | Key Tests |
|-----------|---------|-----------|
| `tests/unit` | Fast, isolated logic checks | `test_memory.py`, `test_agent_core.py` |
| `tests/gateway` | API & WebSocket behavior | `test_gateway.py`, `test_gateway_events.py` |
| `tests/durable` | State persistence | `test_durable_ledger.py`, `test_durable_state.py` |
| `tests/stabilization` | End-to-End Smoke Tests | `test_smoke_direct.py`, `test_smoke_gateway.py` |
| `tests/letta` | Letta Memory integration | `test_letta_client.py` |

## 4. UI Testing
Currently, the UI is tested **manually** following the `UI_Documentation/06_Testing_Guide.md` runbook. Automated UI testing (e.g. Playwright) is planned for future phases.

## 5. Troubleshooting
- **Gateway connection refused**: Ensure no orphaned `uvicorn` processes are running (`pkill -f uvicorn`).
- **DB Locks**: If SQLite errors occur, delete `AGENT_RUN_WORKSPACES/test_*.db`.
