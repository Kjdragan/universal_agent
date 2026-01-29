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

## 4. Phase 0 smoke checklist (parity baseline)
Use this checklist before any behavior changes. It confirms the **same execution path** across CLI direct, gateway, and Web UI.

- [ ] **CLI direct:** run stabilization smoke test (`tests/stabilization/test_smoke_direct.py`).
- [ ] **Gateway:** run stabilization smoke test (`tests/stabilization/test_smoke_gateway.py`).
- [ ] **Web UI:** follow `UI_Documentation/06_Testing_Guide.md` Test 1 + Test 2.
- [ ] Confirm outputs are equivalent (tool call counts, completion status, artifacts).

## 5. UI-agnostic parity matrix (contract)
All interfaces must produce **equivalent results** (same logic + artifacts), with differences only in UI rendering.

| Interface | Entry point | Expected engine | Parity checks |
|----------|-------------|-----------------|---------------|
| CLI direct | `process_turn()` | direct engine | Same output + artifacts as gateway run |
| CLI via gateway | `InProcessGateway.execute()` | gateway engine | Same output + tool calls as CLI direct |
| Web UI | WS `execute` | gateway engine | Same output + artifacts as CLI via gateway |
| Telegram | gateway client | gateway engine | Same output summary + artifacts as Web UI |

## 6. Agent-browser parity workflow (Web UI)
Use `agent-browser` to automate Web UI parity checks when validating gateway output.

**Prereqs:**
- `agent-browser install` (Chromium)
- Gateway + Web UI running

**Workflow (example):**
1. `agent-browser --session ua-test open http://localhost:3000`
2. `agent-browser --session ua-test snapshot -i --json`
3. Identify the chat input ref (from snapshot) and fill it:
   - `agent-browser --session ua-test fill @<chat_input_ref> "What is 2+2?"`
4. Submit (press Enter or click send):
   - `agent-browser --session ua-test press Enter`
5. Wait for response text:
   - `agent-browser --session ua-test wait --text "4"`
6. Capture a screenshot for artifacts:
   - `agent-browser --session ua-test screenshot work_products/ui_parity.png`

**Parity check:**
- Compare response text, tool call count, and artifacts against CLI/gateway runs.
- Re-run snapshot after completion for verification.

## 7. UI Testing
Currently, the UI is tested **manually** following the `UI_Documentation/06_Testing_Guide.md` runbook. Automated UI testing (e.g. Playwright) is planned for future phases.

## 8. Troubleshooting
- **Gateway connection refused**: Ensure no orphaned `uvicorn` processes are running (`pkill -f uvicorn`).
- **DB Locks**: If SQLite errors occur, delete `AGENT_RUN_WORKSPACES/test_*.db`.
