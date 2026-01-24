# Universal Agent Test Suite

## Overview

This directory contains the test suite for the Universal Agent system. Tests are organized by component and include unit tests, integration tests, and regression tests.

## Directory Structure

```
tests/
├── README.md                           # This file
├── conftest.py                         # Pytest configuration and shared fixtures
│
├── # Gateway Tests (Stage 3-6)
├── test_gateway.py                     # Gateway dataclasses and InProcessGateway
├── test_gateway_events.py              # EventType enum and AgentEvent
├── test_gateway_integration.py         # Gateway execution flows, harness integration
├── test_gateway_urw_adapter.py         # GatewayURWAdapter for URW harness
├── test_gateway_worker_pool.py         # Worker pool and lease coordination
│
├── # Durable Execution Tests
├── test_durable_state.py               # Run state management
├── test_durable_ledger.py              # Tool call ledger and idempotency
├── test_durable_classification.py      # Tool call classification
├── test_durable_checkpointing.py       # Checkpoint and resume
├── test_durable_normalize.py           # Path normalization
│
├── # URW (Universal Ralph Wrapper) Tests
├── test_urw_adapter.py                 # URW adapter tests
├── test_interview_flow_integration.py  # Interview flow
├── test_interview_plan_merge.py        # Plan merging
│
├── # Composio Integration Tests
├── test_composio_regression.py         # Composio regression tests
├── test_composio_upload.py             # File upload tests
│
├── # Letta Memory System Tests
├── test_letta_*.py                     # Various Letta memory tests
├── test_memory_system.py               # Memory system tests
│
├── # Tool and Provider Tests
├── test_tool_schema_guardrail.py       # Schema validation
├── test_side_effect_class_guardrail.py # Side effect detection
├── test_forced_tool_matches.py         # Tool matching
├── test_provider_idempotency.py        # Provider tests
│
├── # Other Tests
├── test_crash_hooks.py                 # Crash handling
├── test_identity_registry.py           # Identity management
├── test_telegram_formatter.py          # Telegram formatting
├── test_transcript_builder.py          # Transcript building
├── test_workspace_environment.py       # Workspace tests
│
└── # Test Utilities
    ├── local_agent_workspace/          # Test workspace fixtures
    └── simulated_remote_fs/            # Simulated remote filesystem
```

## Running Tests

### Quick Commands

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src/universal_agent --cov-report=html

# Run specific test file
pytest tests/test_gateway.py -v

# Run specific test class
pytest tests/test_gateway.py::TestGatewaySession -v

# Run specific test
pytest tests/test_gateway.py::TestGatewaySession::test_session_creation -v
```

### Test Categories

```bash
# Gateway tests only (fast unit tests)
pytest tests/test_gateway*.py -v

# Durable execution tests
pytest tests/test_durable*.py -v

# Integration tests (slower, may need external services)
pytest -m integration tests/ -v

# Skip slow tests
pytest tests/ -v -m "not slow"
```

### Test Markers

The following pytest markers are available:

| Marker | Description |
|--------|-------------|
| `@pytest.mark.slow` | Tests that take >10 seconds |
| `@pytest.mark.integration` | Tests requiring external services |
| `@pytest.mark.e2e` | End-to-end tests |
| `@pytest.mark.asyncio` | Async tests (auto-detected) |

## Gateway Test Suite

### test_gateway.py

Unit tests for core gateway dataclasses and `InProcessGateway`:

- `TestGatewaySession` — Session dataclass validation
- `TestGatewayRequest` — Request dataclass validation
- `TestGatewayResult` — Result dataclass validation
- `TestInProcessGateway` — Session creation, resume, execution
- `TestGatewayExecution` — Event streaming, run_query

### test_gateway_events.py

Unit tests for event types:

- `TestEventType` — All event type enum values
- `TestAgentEvent` — Event creation and serialization

### test_gateway_integration.py

Integration tests for gateway flows:

- `TestGatewaySessionManagement` — Session persistence
- `TestGatewayEventStream` — Event streaming
- `TestHarnessGatewayIntegration` — URW harness with gateway
- `TestExternalGatewayIntegration` — External gateway (requires server)
- `TestGatewayErrorHandling` — Error cases

### test_gateway_urw_adapter.py

Tests for `GatewayURWAdapter`:

- `TestAdapterFactory` — Adapter creation via factory
- `TestGatewayURWAdapter` — Adapter lifecycle, session management
- `TestGatewayURWAdapterExecution` — Event collection, execution

### test_gateway_worker_pool.py

Tests for worker pool (Stage 6):

- `TestWorkerConfig` — Worker configuration
- `TestPoolConfig` — Pool configuration
- `TestWorkerStatus` — Worker status enum
- `TestWorkerState` — Worker state tracking
- `TestWorker` — Worker lifecycle
- `TestWorkerPoolManager` — Pool management, scaling
- `TestQueueRun` — Run queueing

## Writing Tests

### Conventions

1. **File naming**: `test_<component>.py`
2. **Class naming**: `Test<Component>` or `Test<Feature>`
3. **Method naming**: `test_<what_is_being_tested>`
4. **Use fixtures** for common setup (see `conftest.py`)
5. **Mock external services** to keep tests fast and reliable

### Example Test

```python
import pytest
from universal_agent.gateway import GatewaySession

class TestGatewaySession:
    """Tests for GatewaySession dataclass."""

    def test_session_creation(self):
        """Test creating a session with required fields."""
        session = GatewaySession(
            session_id="sess_abc123",
            user_id="user_1",
            workspace_dir="/tmp/workspace",
        )
        
        assert session.session_id == "sess_abc123"
        assert session.user_id == "user_1"
        assert session.metadata == {}

    @pytest.mark.asyncio
    async def test_async_operation(self, gateway, tmp_path):
        """Test an async gateway operation."""
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        assert session.session_id is not None
```

### Fixtures

Common fixtures in `conftest.py`:

```python
@pytest.fixture
def temp_workspace(tmp_path):
    """Create temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
```

## CI Integration

Tests run automatically on:
- Push to `main` or `dev-*` branches
- Pull requests

See `.github/workflows/` for CI configuration.

## Troubleshooting

### Common Issues

**Import errors**: Ensure `PYTHONPATH` includes `src/`:
```bash
PYTHONPATH=src pytest tests/ -v
```

**Async test failures**: Ensure `pytest-asyncio` is installed and tests use `@pytest.mark.asyncio`.

**Slow tests**: Use `-m "not slow"` to skip slow tests during development.

**External service tests**: Tests marked `@pytest.mark.integration` may require external services (e.g., gateway server at `localhost:8002`).
