# UA Gateway Refactor Workspace

This directory contains documentation and validation artifacts for the UA Gateway refactor project.

## Directory Structure

```
Refactor_Workspace/
├── README.md                           # This file
├── docs/                               # Main documentation (numbered)
│   ├── 00_INDEX.md                     # Documentation index
│   ├── 01_ARCHITECTURE_OVERVIEW.md     # High-level architecture
│   ├── 02_GATEWAY_API.md               # Gateway API reference
│   ├── 03_EVENT_STREAM.md              # Event types and streaming
│   ├── 04_EXTERNAL_GATEWAY.md          # External gateway server/client
│   ├── 05_URW_INTEGRATION.md           # URW harness integration
│   ├── 06_WORKER_POOL.md               # Worker pool and leases
│   ├── 07_SEQUENCE_DIAGRAMS.md         # Mermaid sequence diagrams
│   └── 08_TESTING_GUIDE.md             # Test organization guide
├── stage2_validation/                  # Parity validation logs and diffs
│   ├── cli_default_*.log               # CLI default mode logs
│   ├── cli_gateway_*.log               # Gateway mode logs
│   ├── cli_vs_gateway_*.diff           # Parity comparison diffs
│   └── job_*.json                      # Job mode test files
└── archive/                            # Historical tracking documents
    ├── ua_gateway_refactor_plan.md     # Original refactor plan
    ├── ua_gateway_refactor_progress.md # Progress log
    ├── ua_gateway_outstanding_work.md  # Work tracker
    ├── ua_gateway_guardrails_checklist.md
    ├── ua_gateway_handoff_context.md
    ├── ua_gateway_smoke_tests.md
    └── ua_gateway_stage4_externalization.md
```

## Quick Links

- **[Documentation Index](docs/00_INDEX.md)** — Start here for architecture and API docs
- **[Sequence Diagrams](docs/07_SEQUENCE_DIAGRAMS.md)** — Visual flow diagrams
- **[Testing Guide](docs/08_TESTING_GUIDE.md)** — How to run tests

## Project Status

All 6 stages of the UA Gateway refactor are **complete**:

| Stage | Description | Status |
|-------|-------------|--------|
| 1 | Dependency Hardening | ✅ |
| 2 | Event Stream Normalization | ✅ |
| 3 | Gateway API In-Process | ✅ |
| 4 | Gateway Externalization | ✅ |
| 5 | URW Integration | ✅ |
| 6 | Worker Pool + Lease Durability | ✅ |

## Key Implementation Files

| File | Description |
|------|-------------|
| `src/universal_agent/gateway.py` | Gateway interface and implementations |
| `src/universal_agent/gateway_server.py` | External gateway FastAPI server |
| `src/universal_agent/agent_core.py` | EventType enum |
| `src/universal_agent/urw/integration.py` | GatewayURWAdapter |
| `src/universal_agent/urw/harness_orchestrator.py` | Harness with gateway mode |
| `src/universal_agent/durable/worker_pool.py` | Worker pool manager |

## Test Files

Gateway-specific tests in `tests/`:

- `test_gateway.py` — Gateway interface unit tests
- `test_gateway_events.py` — Event type unit tests
- `test_gateway_worker_pool.py` — Worker pool unit tests
- `test_gateway_urw_adapter.py` — URW adapter unit tests
- `test_gateway_integration.py` — Integration tests

Run tests:

```bash
# All gateway tests
pytest tests/test_gateway*.py -v

# Just unit tests (fast)
pytest tests/test_gateway.py tests/test_gateway_events.py tests/test_gateway_worker_pool.py -v
```
