"""
Unit tests for Worker Pool and Lease Durability (Stage 6).

Tests cover:
- WorkerConfig and PoolConfig
- Worker lifecycle
- WorkerPoolManager
- Lease acquisition helpers
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from universal_agent.durable.worker_pool import (
        Worker,
        WorkerConfig,
        WorkerPoolManager,
        PoolConfig,
        WorkerStatus,
        WorkerState,
        queue_run,
    )
    WORKER_POOL_AVAILABLE = True
except ImportError:
    WORKER_POOL_AVAILABLE = False


@pytest.mark.skipif(not WORKER_POOL_AVAILABLE, reason="Worker pool module not available")
class TestWorkerConfig:
    """Tests for WorkerConfig dataclass."""

    def test_default_config(self):
        """Test default worker configuration values."""
        config = WorkerConfig()
        
        assert config.lease_ttl_seconds == 60
        assert config.heartbeat_interval_seconds == 15
        assert config.poll_interval_seconds == 5
        assert config.max_concurrent_runs == 1
        assert config.gateway_url is None
        assert config.worker_id.startswith("worker_")

    def test_custom_config(self):
        """Test custom worker configuration."""
        config = WorkerConfig(
            worker_id="custom_worker_001",
            lease_ttl_seconds=120,
            heartbeat_interval_seconds=30,
            poll_interval_seconds=10,
            max_concurrent_runs=2,
            gateway_url="http://localhost:8002",
        )
        
        assert config.worker_id == "custom_worker_001"
        assert config.lease_ttl_seconds == 120
        assert config.heartbeat_interval_seconds == 30
        assert config.poll_interval_seconds == 10
        assert config.max_concurrent_runs == 2
        assert config.gateway_url == "http://localhost:8002"

    def test_worker_id_uniqueness(self):
        """Test that default worker IDs are unique."""
        config1 = WorkerConfig()
        config2 = WorkerConfig()
        
        assert config1.worker_id != config2.worker_id


@pytest.mark.skipif(not WORKER_POOL_AVAILABLE, reason="Worker pool module not available")
class TestPoolConfig:
    """Tests for PoolConfig dataclass."""

    def test_default_config(self):
        """Test default pool configuration values."""
        config = PoolConfig()
        
        assert config.db_path == "runtime.db"
        assert config.min_workers == 1
        assert config.max_workers == 4
        assert config.scale_up_threshold == 5
        assert config.scale_down_idle_seconds == 300

    def test_custom_config(self):
        """Test custom pool configuration."""
        config = PoolConfig(
            db_path="/custom/path/runtime.db",
            min_workers=2,
            max_workers=8,
            scale_up_threshold=10,
            scale_down_idle_seconds=600,
        )
        
        assert config.db_path == "/custom/path/runtime.db"
        assert config.min_workers == 2
        assert config.max_workers == 8
        assert config.scale_up_threshold == 10
        assert config.scale_down_idle_seconds == 600


@pytest.mark.skipif(not WORKER_POOL_AVAILABLE, reason="Worker pool module not available")
class TestWorkerStatus:
    """Tests for WorkerStatus enum."""

    def test_status_values(self):
        """Test all worker status values."""
        assert WorkerStatus.IDLE.value == "idle"
        assert WorkerStatus.PROCESSING.value == "processing"
        assert WorkerStatus.DRAINING.value == "draining"
        assert WorkerStatus.STOPPED.value == "stopped"

    def test_status_is_string_enum(self):
        """Test that WorkerStatus inherits from str."""
        assert isinstance(WorkerStatus.IDLE, str)
        assert WorkerStatus.IDLE == "idle"


@pytest.mark.skipif(not WORKER_POOL_AVAILABLE, reason="Worker pool module not available")
class TestWorkerState:
    """Tests for WorkerState dataclass."""

    def test_default_state(self):
        """Test default worker state."""
        state = WorkerState(worker_id="worker_001")
        
        assert state.worker_id == "worker_001"
        assert state.status == WorkerStatus.IDLE
        assert state.current_run_id is None
        assert state.runs_completed == 0
        assert state.runs_failed == 0
        assert isinstance(state.started_at, datetime)
        assert isinstance(state.last_heartbeat, datetime)

    def test_custom_state(self):
        """Test worker state with custom values."""
        state = WorkerState(
            worker_id="worker_002",
            status=WorkerStatus.PROCESSING,
            current_run_id="run_abc",
            runs_completed=10,
            runs_failed=2,
        )
        
        assert state.status == WorkerStatus.PROCESSING
        assert state.current_run_id == "run_abc"
        assert state.runs_completed == 10
        assert state.runs_failed == 2


@pytest.mark.skipif(not WORKER_POOL_AVAILABLE, reason="Worker pool module not available")
class TestWorker:
    """Tests for Worker class."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database connection."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        conn.execute.return_value.fetchone.return_value = None
        conn.execute.return_value.rowcount = 0
        return conn

    @pytest.fixture
    def mock_run_handler(self):
        """Create mock run handler that succeeds."""
        return AsyncMock(return_value=True)

    @pytest.fixture
    def worker(self, mock_db, mock_run_handler):
        """Create a worker for testing."""
        config = WorkerConfig(worker_id="test_worker")
        return Worker(config, mock_db, mock_run_handler)

    def test_worker_creation(self, worker):
        """Test worker is created with correct initial state."""
        assert worker.config.worker_id == "test_worker"
        assert worker.state.status == WorkerStatus.IDLE
        assert worker.state.current_run_id is None

    @pytest.mark.asyncio
    async def test_worker_start(self, worker):
        """Test worker start creates background tasks."""
        await worker.start()
        
        assert worker.state.status == WorkerStatus.IDLE
        assert worker._heartbeat_task is not None
        assert worker._process_task is not None
        
        # Cleanup
        await worker.stop(drain=False)

    @pytest.mark.asyncio
    async def test_worker_stop_no_drain(self, worker):
        """Test worker stop without draining."""
        await worker.start()
        await worker.stop(drain=False)
        
        assert worker.state.status == WorkerStatus.STOPPED

    @pytest.mark.asyncio
    async def test_worker_stop_with_drain(self, worker):
        """Test worker stop with draining waits for current work."""
        await worker.start()
        
        # No current work, so drain should complete immediately
        await worker.stop(drain=True)
        
        assert worker.state.status == WorkerStatus.STOPPED


@pytest.mark.skipif(not WORKER_POOL_AVAILABLE, reason="Worker pool module not available")
class TestWorkerPoolManager:
    """Tests for WorkerPoolManager class."""

    @pytest.fixture
    def mock_run_handler(self):
        """Create mock run handler."""
        return AsyncMock(return_value=True)

    @pytest.mark.asyncio
    async def test_pool_creation(self, mock_run_handler):
        """Test pool manager creation."""
        config = PoolConfig(min_workers=2, max_workers=4)
        pool = WorkerPoolManager(config, run_handler=mock_run_handler)
        
        assert pool.pool_config.min_workers == 2
        assert pool.pool_config.max_workers == 4
        assert len(pool.workers) == 0  # Not started yet

    @pytest.mark.asyncio
    async def test_pool_start_creates_workers(self, mock_run_handler):
        """Test that starting pool creates minimum workers."""
        with patch('universal_agent.durable.worker_pool.connect_runtime_db') as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_connect.return_value = mock_conn
            
            config = PoolConfig(min_workers=2, max_workers=4)
            pool = WorkerPoolManager(config, run_handler=mock_run_handler)
            
            await pool.start()
            
            assert len(pool.workers) == 2
            
            await pool.stop(drain=False)

    @pytest.mark.asyncio
    async def test_pool_stop_removes_workers(self, mock_run_handler):
        """Test that stopping pool removes all workers."""
        with patch('universal_agent.durable.worker_pool.connect_runtime_db') as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_connect.return_value = mock_conn
            
            config = PoolConfig(min_workers=1, max_workers=2)
            pool = WorkerPoolManager(config, run_handler=mock_run_handler)
            
            await pool.start()
            assert len(pool.workers) == 1
            
            await pool.stop(drain=False)
            assert len(pool.workers) == 0

    def test_get_pool_stats_empty(self, mock_run_handler):
        """Test pool stats when pool hasn't started."""
        config = PoolConfig()
        pool = WorkerPoolManager(config, run_handler=mock_run_handler)
        
        stats = pool.get_pool_stats()
        
        assert stats["total_workers"] == 0
        assert stats["idle_workers"] == 0
        assert stats["processing_workers"] == 0
        assert stats["total_completed"] == 0
        assert stats["total_failed"] == 0

    @pytest.mark.asyncio
    async def test_get_pool_stats_with_workers(self, mock_run_handler):
        """Test pool stats with active workers."""
        with patch('universal_agent.durable.worker_pool.connect_runtime_db') as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_connect.return_value = mock_conn
            
            config = PoolConfig(min_workers=2, max_workers=4)
            pool = WorkerPoolManager(config, run_handler=mock_run_handler)
            
            await pool.start()
            
            stats = pool.get_pool_stats()
            
            assert stats["total_workers"] == 2
            assert "workers" in stats
            
            await pool.stop(drain=False)


@pytest.mark.skipif(not WORKER_POOL_AVAILABLE, reason="Worker pool module not available")
class TestQueueRun:
    """Tests for queue_run helper function."""

    def test_queue_run_calls_upsert(self):
        """Test that queue_run calls upsert_run with correct parameters."""
        mock_conn = MagicMock()
        
        with patch('universal_agent.durable.state.upsert_run') as mock_upsert:
            queue_run(
                mock_conn,
                run_id="job_123",
                prompt="Test prompt",
                workspace_dir="/tmp/workspace",
                max_iterations=50,
            )
            
            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args
            
            assert call_kwargs[1]["run_id"] == "job_123"
            assert call_kwargs[1]["status"] == "queued"
            assert call_kwargs[1]["last_job_prompt"] == "Test prompt"
