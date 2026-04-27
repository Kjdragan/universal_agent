import json
from pathlib import Path
import shutil
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock dependencies before import
sys.modules["universal_agent.durable.db"] = MagicMock()
sys.modules["universal_agent.durable.migrations"] = MagicMock()
sys.modules["logfire"] = MagicMock()

from universal_agent.heartbeat_service import GatewaySession, HeartbeatService


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
        service.wake_sessions.add(session_id)
        service.last_wake_reason[session_id] = "test"
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


@pytest.mark.asyncio
async def test_managed_heartbeat_session_refreshes_from_global(tmp_path, mock_gateway, mock_connection_manager):
    """Managed Simone heartbeat sessions should track the current global file."""

    session_id = "session_hook_simone_heartbeat_ntf_test"
    workspace = tmp_path / session_id
    workspace.mkdir()

    stale_hb = workspace / "HEARTBEAT.md"
    stale_hb.write_text("# Old Instructions", encoding="utf-8")
    mem_dir = workspace / "memory"
    mem_dir.mkdir()
    (mem_dir / "HEARTBEAT.md").write_text("# Older Instructions", encoding="utf-8")

    global_memory = tmp_path / "memory_global"
    global_memory.mkdir()
    global_hb = global_memory / "HEARTBEAT.md"
    global_hb.write_text("# Current Global Instructions", encoding="utf-8")

    with patch("universal_agent.heartbeat_service.GLOBAL_HEARTBEAT_PATH", global_hb):
        service = HeartbeatService(mock_gateway, mock_connection_manager)
        session = GatewaySession(
            session_id=session_id,
            user_id="u1",
            workspace_dir=str(workspace),
            metadata={},
        )

        service.wake_sessions.add(session_id)
        service.last_wake_reason[session_id] = "test"
        with patch.object(service, "_run_heartbeat", new_callable=MagicMock):
            await service._process_session(session)

        assert stale_hb.read_text(encoding="utf-8") == "# Current Global Instructions"
        assert (mem_dir / "HEARTBEAT.md").read_text(encoding="utf-8") == "# Current Global Instructions"


@pytest.mark.asyncio
async def test_heartbeat_empty_content_records_skip_marker(tmp_path, mock_gateway, mock_connection_manager):
    """Empty HEARTBEAT.md should persist an explicit skip marker for UI visibility."""
    session_id = "test_session_empty_hb"
    workspace = tmp_path / session_id
    workspace.mkdir()
    (workspace / "HEARTBEAT.md").write_text("- [ ]", encoding="utf-8")

    service = HeartbeatService(mock_gateway, mock_connection_manager)
    session = GatewaySession(
        session_id=session_id,
        user_id="u1",
        workspace_dir=str(workspace),
        metadata={},
    )

    service.wake_sessions.add(session_id)
    service.last_wake_reason[session_id] = "test"
    with patch.object(service, "_run_heartbeat", new_callable=MagicMock) as mock_run:
        await service._process_session(session)
        mock_run.assert_not_called()

    state_path = workspace / "heartbeat_state.json"
    assert state_path.exists()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    summary = payload.get("last_summary") or {}
    assert summary.get("suppressed_reason") == "empty_content"
    assert "Heartbeat skipped: empty HEARTBEAT.md content." in str(summary.get("text") or "")
