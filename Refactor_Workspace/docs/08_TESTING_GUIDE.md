# Testing Guide

## Overview

This guide covers the test organization and execution for the UA Gateway system. Tests are organized into:

- **Unit Tests** — Isolated component testing
- **Integration Tests** — Component interaction testing
- **End-to-End Tests** — Full flow validation

---

## Test Directory Structure

```
tests/
├── unit/
│   ├── test_gateway.py           # Gateway interface tests
│   ├── test_gateway_session.py   # Session management tests
│   ├── test_event_types.py       # Event type tests
│   ├── test_worker_pool.py       # Worker pool tests
│   └── test_urw_adapter.py       # URW adapter tests
├── integration/
│   ├── test_gateway_execution.py # Gateway execution flows
│   ├── test_external_gateway.py  # External gateway client/server
│   ├── test_urw_gateway.py       # URW through gateway
│   └── test_worker_execution.py  # Worker pool execution
└── e2e/
    ├── test_cli_gateway.py       # CLI with gateway
    └── test_distributed.py       # Multi-worker scenarios
```

---

## Running Tests

### All Tests

```bash
# From project root
pytest tests/ -v

# With coverage
pytest tests/ --cov=src/universal_agent --cov-report=html
```

### Specific Categories

```bash
# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# E2E tests only
pytest tests/e2e/ -v
```

### Specific Files

```bash
# Single test file
pytest tests/unit/test_gateway.py -v

# Single test function
pytest tests/unit/test_gateway.py::test_create_session -v
```

---

## Unit Tests

### test_gateway.py

Tests for `InProcessGateway` core functionality.

```python
import pytest
from universal_agent.gateway import InProcessGateway, GatewayRequest, GatewaySession

@pytest.fixture
async def gateway():
    """Create a fresh gateway for each test."""
    gw = InProcessGateway()
    yield gw
    # Cleanup sessions if needed

class TestInProcessGateway:
    
    @pytest.mark.asyncio
    async def test_create_session(self, gateway):
        """Test session creation."""
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir="/tmp/test_workspace",
        )
        
        assert session.session_id is not None
        assert session.user_id == "test_user"
        assert session.workspace_dir == "/tmp/test_workspace"
    
    @pytest.mark.asyncio
    async def test_create_session_auto_workspace(self, gateway):
        """Test session creation with auto-generated workspace."""
        session = await gateway.create_session(user_id="test_user")
        
        assert session.workspace_dir is not None
        assert "gateway_session" in session.workspace_dir
    
    @pytest.mark.asyncio
    async def test_get_session(self, gateway):
        """Test retrieving existing session."""
        created = await gateway.create_session(user_id="test_user")
        retrieved = await gateway.get_session(created.session_id)
        
        assert retrieved is not None
        assert retrieved.session_id == created.session_id
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, gateway):
        """Test retrieving non-existent session returns None."""
        session = await gateway.get_session("nonexistent_id")
        assert session is None
    
    @pytest.mark.asyncio
    async def test_list_sessions(self, gateway):
        """Test listing sessions."""
        await gateway.create_session(user_id="user1")
        await gateway.create_session(user_id="user1")
        await gateway.create_session(user_id="user2")
        
        all_sessions = await gateway.list_sessions()
        assert len(all_sessions) == 3
        
        user1_sessions = await gateway.list_sessions(user_id="user1")
        assert len(user1_sessions) == 2
```

### test_event_types.py

Tests for event type definitions.

```python
import pytest
from universal_agent.agent_core import EventType, AgentEvent
from datetime import datetime, timezone

class TestEventType:
    
    def test_event_type_values(self):
        """Test all event types have expected string values."""
        assert EventType.TEXT.value == "text"
        assert EventType.TOOL_CALL.value == "tool_call"
        assert EventType.TOOL_RESULT.value == "tool_result"
        assert EventType.ERROR.value == "error"
        
    def test_urw_event_types(self):
        """Test URW-specific event types exist."""
        assert EventType.URW_PHASE_START.value == "urw_phase_start"
        assert EventType.URW_PHASE_COMPLETE.value == "urw_phase_complete"
        assert EventType.URW_PHASE_FAILED.value == "urw_phase_failed"
        assert EventType.URW_EVALUATION.value == "urw_evaluation"

class TestAgentEvent:
    
    def test_event_creation(self):
        """Test creating an event."""
        event = AgentEvent(
            type=EventType.TEXT,
            data={"text": "Hello"},
        )
        
        assert event.type == EventType.TEXT
        assert event.data["text"] == "Hello"
        assert event.timestamp is not None
    
    def test_event_serialization(self):
        """Test event can be serialized to dict."""
        event = AgentEvent(
            type=EventType.TOOL_CALL,
            data={"id": "call_1", "name": "ListDir"},
        )
        
        serialized = {
            "type": event.type.value,
            "data": event.data,
            "timestamp": event.timestamp.isoformat(),
        }
        
        assert serialized["type"] == "tool_call"
        assert serialized["data"]["name"] == "ListDir"
```

### test_worker_pool.py

Tests for worker pool components.

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from universal_agent.durable.worker_pool import (
    Worker, WorkerConfig, WorkerPoolManager, PoolConfig, WorkerStatus
)

@pytest.fixture
def mock_db():
    """Create mock database connection."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.fetchone.return_value = None
    return conn

@pytest.fixture
def mock_run_handler():
    """Create mock run handler."""
    return AsyncMock(return_value=True)

class TestWorkerConfig:
    
    def test_default_config(self):
        """Test default worker configuration."""
        config = WorkerConfig()
        
        assert config.lease_ttl_seconds == 60
        assert config.heartbeat_interval_seconds == 15
        assert config.poll_interval_seconds == 5
        assert config.worker_id.startswith("worker_")
    
    def test_custom_config(self):
        """Test custom worker configuration."""
        config = WorkerConfig(
            worker_id="custom_worker",
            lease_ttl_seconds=120,
            gateway_url="http://localhost:8002",
        )
        
        assert config.worker_id == "custom_worker"
        assert config.lease_ttl_seconds == 120
        assert config.gateway_url == "http://localhost:8002"

class TestWorker:
    
    @pytest.mark.asyncio
    async def test_worker_start_stop(self, mock_db, mock_run_handler):
        """Test worker start and stop."""
        config = WorkerConfig(worker_id="test_worker")
        worker = Worker(config, mock_db, mock_run_handler)
        
        await worker.start()
        assert worker.state.status == WorkerStatus.IDLE
        
        await worker.stop(drain=False)
        assert worker.state.status == WorkerStatus.STOPPED
    
    @pytest.mark.asyncio
    async def test_worker_state_tracking(self, mock_db, mock_run_handler):
        """Test worker state is tracked correctly."""
        config = WorkerConfig(worker_id="test_worker")
        worker = Worker(config, mock_db, mock_run_handler)
        
        assert worker.state.runs_completed == 0
        assert worker.state.runs_failed == 0
        assert worker.state.current_run_id is None

class TestPoolConfig:
    
    def test_default_pool_config(self):
        """Test default pool configuration."""
        config = PoolConfig()
        
        assert config.min_workers == 1
        assert config.max_workers == 4
        assert config.scale_up_threshold == 5

class TestWorkerPoolManager:
    
    @pytest.mark.asyncio
    async def test_pool_start_stop(self, mock_run_handler):
        """Test pool start and stop."""
        with patch('universal_agent.durable.worker_pool.connect_runtime_db') as mock_connect:
            mock_connect.return_value = MagicMock()
            
            pool_config = PoolConfig(min_workers=1, max_workers=2)
            pool = WorkerPoolManager(pool_config, run_handler=mock_run_handler)
            
            await pool.start()
            assert len(pool.workers) == 1
            
            await pool.stop(drain=False)
            assert len(pool.workers) == 0
    
    def test_get_pool_stats(self):
        """Test pool statistics."""
        pool_config = PoolConfig()
        pool = WorkerPoolManager(pool_config)
        
        stats = pool.get_pool_stats()
        
        assert "total_workers" in stats
        assert "idle_workers" in stats
        assert "processing_workers" in stats
        assert "total_completed" in stats
```

---

## Integration Tests

### test_gateway_execution.py

Tests for gateway execution flows.

```python
import pytest
from universal_agent.gateway import InProcessGateway, GatewayRequest
from universal_agent.agent_core import EventType

class TestGatewayExecution:
    
    @pytest.fixture
    async def gateway_with_session(self, tmp_path):
        """Create gateway with active session."""
        gateway = InProcessGateway()
        session = await gateway.create_session(
            user_id="test_user",
            workspace_dir=str(tmp_path),
        )
        return gateway, session
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_simple_query(self, gateway_with_session):
        """Test simple query execution."""
        gateway, session = gateway_with_session
        request = GatewayRequest(user_input="What is 2 + 2?")
        
        events = []
        async for event in gateway.execute(session, request):
            events.append(event)
        
        # Should have at least text and iteration_end events
        event_types = [e.type for e in events]
        assert EventType.TEXT in event_types or EventType.ITERATION_END in event_types
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_tool_execution(self, gateway_with_session, tmp_path):
        """Test query that triggers tool use."""
        gateway, session = gateway_with_session
        
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World")
        
        request = GatewayRequest(user_input=f"Read the file {test_file}")
        
        events = []
        async for event in gateway.execute(session, request):
            events.append(event)
        
        event_types = [e.type for e in events]
        # Should include tool call for reading file
        assert EventType.TOOL_CALL in event_types or EventType.TEXT in event_types
    
    @pytest.mark.asyncio
    async def test_run_query_blocking(self, gateway_with_session):
        """Test blocking run_query method."""
        gateway, session = gateway_with_session
        request = GatewayRequest(user_input="Say hello")
        
        response = await gateway.run_query(session, request)
        
        assert response.session_id == session.session_id
        assert response.output is not None

### test_external_gateway.py

Tests for external gateway client/server interaction.

```python
import pytest
import asyncio
from unittest.mock import patch, AsyncMock

class TestExternalGatewayClient:
    
    @pytest.mark.asyncio
    async def test_client_creation(self):
        """Test external gateway client creation."""
        from universal_agent.gateway import ExternalGateway
        
        gateway = ExternalGateway(base_url="http://localhost:8002")
        assert gateway.base_url == "http://localhost:8002"
        await gateway.close()
    
    @pytest.mark.asyncio
    async def test_client_context_manager(self):
        """Test client as context manager."""
        from universal_agent.gateway import ExternalGateway
        
        async with ExternalGateway("http://localhost:8002") as gateway:
            assert gateway is not None

# Note: Full integration tests require running gateway server
# These are marked for manual execution or CI with server setup

@pytest.mark.integration
class TestExternalGatewayIntegration:
    
    @pytest.fixture
    async def running_server(self):
        """Start gateway server for tests."""
        # This would start the actual server
        # For CI, use docker-compose or similar
        pass
    
    @pytest.mark.asyncio
    async def test_create_session_remote(self, running_server):
        """Test creating session on remote server."""
        pass  # Implement with actual server
```

---

## End-to-End Tests

### test_cli_gateway.py

Tests for CLI with gateway enabled.

```python
import pytest
import subprocess
import os

class TestCLIGateway:
    
    @pytest.mark.e2e
    def test_cli_with_gateway_flag(self, tmp_path):
        """Test CLI with --use-gateway flag."""
        result = subprocess.run(
            [
                "python", "-m", "universal_agent",
                "--use-gateway",
                "--max-iterations", "1",
                "Say hello",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        # Check for gateway banner or successful output
        assert result.returncode == 0 or "error" not in result.stderr.lower()
    
    @pytest.mark.e2e
    def test_cli_with_gateway_env(self, tmp_path):
        """Test CLI with UA_USE_GATEWAY environment variable."""
        env = os.environ.copy()
        env["UA_USE_GATEWAY"] = "1"
        
        result = subprocess.run(
            [
                "python", "-m", "universal_agent",
                "--max-iterations", "1",
                "Say hello",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        
        assert result.returncode == 0 or "error" not in result.stderr.lower()
```

---

## Test Fixtures

### conftest.py

Shared fixtures for all tests.

```python
import pytest
import tempfile
import shutil
from pathlib import Path

@pytest.fixture
def temp_workspace():
    """Create temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="ua_test_")
    yield Path(workspace)
    shutil.rmtree(workspace, ignore_errors=True)

@pytest.fixture
def mock_llm_response():
    """Mock LLM API response."""
    return {
        "content": [{"type": "text", "text": "Hello, I'm Claude!"}],
        "stop_reason": "end_turn",
    }

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

# Markers
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks integration tests")
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")
```

---

## Test Coverage Goals

| Component | Target Coverage |
|-----------|-----------------|
| gateway.py | 80% |
| gateway_server.py | 75% |
| agent_core.py (EventType) | 100% |
| urw/integration.py (GatewayURWAdapter) | 75% |
| durable/worker_pool.py | 80% |
| durable/state.py (lease functions) | 90% |

---

## CI Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e ".[dev]"
      
      - name: Run unit tests
        run: pytest tests/unit/ -v --cov=src/universal_agent
      
      - name: Run integration tests
        run: pytest tests/integration/ -v -m "not slow"
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```
