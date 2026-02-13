import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import shutil

# Mock dependencies before import
sys.modules["universal_agent.durable.db"] = MagicMock()
sys.modules["universal_agent.durable.migrations"] = MagicMock()
sys.modules["logfire"] = MagicMock()

from universal_agent.heartbeat_service import HeartbeatService, GatewaySession

@pytest.fixture
def mock_gateway():
    return MagicMock()

@pytest.fixture
def mock_connection_manager():
    cm = MagicMock()
    cm.session_connections = {}
    return cm

@pytest.mark.asyncio
async def test_heartbeat_seeds_file(tmp_path, mock_gateway, mock_connection_manager):
    """Verify that HEARTBEAT.md is seeded from global memory if missing."""
    
    # Setup paths
    session_id = "test_session_seed"
    workspace = tmp_path / session_id
    workspace.mkdir()
    
    # Create fake global heartbeat file
    global_memory = tmp_path / "memory"
    global_memory.mkdir()
    global_hb = global_memory / "HEARTBEAT.md"
    global_hb.write_text("# Global Heartbeat Instructions")
    
    # Mock PROJECT_ROOT/GLOBAL_HEARTBEAT_PATH in the service module
    with patch("universal_agent.heartbeat_service.GLOBAL_HEARTBEAT_PATH", global_hb):
        service = HeartbeatService(mock_gateway, mock_connection_manager)
        
        # Create session object
        session = GatewaySession(
            session_id=session_id,
            user_id="u1",
            workspace_dir=str(workspace),
            metadata={}
        )
        service.active_sessions[session_id] = session
        
        # Verify file does not exist yet
        assert not (workspace / "HEARTBEAT.md").exists()
        
        # Trigger processing (should seed file)
        # We assume _process_session is called. 
        # Since _process_session is async and might call _run_heartbeat, 
        # we'll mock _run_heartbeat to avoid complexity
        with patch.object(service, "_run_heartbeat", new_callable=MagicMock) as mock_run:
             await service._process_session(session)
        
        # Verify file WAS seeded
        seeded_file = workspace / "HEARTBEAT.md"
        assert seeded_file.exists()
        assert seeded_file.read_text() == "# Global Heartbeat Instructions"

        # Also seed into workspace/memory/ for tooling conventions.
        seeded_mem_file = workspace / "memory" / "HEARTBEAT.md"
        assert seeded_mem_file.exists()
        assert seeded_mem_file.read_text() == "# Global Heartbeat Instructions"

@pytest.mark.asyncio
async def test_heartbeat_does_not_overwrite(tmp_path, mock_gateway, mock_connection_manager):
    """Verify that existing HEARTBEAT.md is NOT overwritten."""
    
    # Setup
    session_id = "test_session_existing"
    workspace = tmp_path / session_id
    workspace.mkdir()
    
    # Existig file
    existing_hb = workspace / "HEARTBEAT.md"
    existing_hb.write_text("# Existing Instructions")
    
    # Fake global
    global_memory = tmp_path / "memory"
    global_memory.mkdir()
    global_hb = global_memory / "HEARTBEAT.md"
    global_hb.write_text("# Global Heartbeat Instructions")
    
    with patch("universal_agent.heartbeat_service.GLOBAL_HEARTBEAT_PATH", global_hb):
        service = HeartbeatService(mock_gateway, mock_connection_manager)
        
        session = GatewaySession(
            session_id=session_id,
            user_id="u1",
            workspace_dir=str(workspace),
            metadata={}
        )
        
        with patch.object(service, "_run_heartbeat", new_callable=MagicMock):
             await service._process_session(session)
             
        # Verify NOT overwritten
        assert existing_hb.read_text() == "# Existing Instructions"
