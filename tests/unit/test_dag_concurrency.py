import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from universal_agent.vp.profiles import VpProfile
from universal_agent.vp.worker_loop import VpWorkerLoop


@pytest.mark.asyncio
async def test_vp_coder_respects_dag_concurrency():
    """
    Verify that VP coder tasks are throttled by UA_DAG_MAX_CONCURRENCY.
    If we simulate 3 concurrent _tick-like executions of run_mission,
    only 2 should proceed immediately, and the 3rd should wait.
    """
    profile = VpProfile(
        vp_id="vp.coder.primary",
        display_name="CODIE",
        runtime_id="runtime.coder.external",
        client_kind="claude_code",
        workspace_root=Path("/tmp/workspaces"),
    )
    
    conn = MagicMock()
    
    with patch("universal_agent.vp.worker_loop.get_vp_profile", return_value=profile):
        loop_obj = VpWorkerLoop(
            conn=conn,
            vp_id="vp.coder.primary",
            worker_id="worker-1",
            workspace_base=Path("/tmp")
        )
    
    from unittest.mock import AsyncMock
    mock_client = MagicMock()
    mock_client.run_mission = AsyncMock()
    
    loop_obj._select_client_for_mission = MagicMock(return_value=mock_client)
    
    # We mock _provision_workspace and _teardown_workspace to avoid git subprocesses
    loop_obj._provision_workspace = AsyncMock(return_value="/tmp/ws")
    loop_obj._teardown_workspace = AsyncMock()
    
    # We also need to mock heartbeat stuff
    with patch("universal_agent.vp.worker_loop.heartbeat_vp_mission_claim"), \
         patch("universal_agent.vp.worker_loop.heartbeat_vp_session_lease"), \
         patch("universal_agent.vp.worker_loop.finalize_vp_mission"), \
         patch("universal_agent.vp.worker_loop.append_vp_event"), \
         patch("universal_agent.vp.worker_loop._write_vp_finalize_artifacts", return_value={}), \
         patch("universal_agent.vp.worker_loop.VpWorkerLoop._write_mission_briefing"):
         
        # We want to measure concurrency
        concurrent_executions = 0
        max_observed_concurrency = 0
        
        async def slow_run_mission(*args, **kwargs):
            nonlocal concurrent_executions, max_observed_concurrency
            concurrent_executions += 1
            max_observed_concurrency = max(max_observed_concurrency, concurrent_executions)
            await asyncio.sleep(0.1)
            concurrent_executions -= 1
            
            # Return a valid mock outcome
            outcome = MagicMock()
            outcome.status = "completed"
            outcome.payload = {}
            outcome.message = ""
            outcome.result_ref = None
            return outcome
            
        mock_client.run_mission.side_effect = slow_run_mission
        
        # We temporarily set the env var and re-initialize the governor
        os.environ["UA_DAG_MAX_CONCURRENCY"] = "2"
        # Reset the governor singleton if it exists
        try:
            from universal_agent.services.dag_governor import DagConcurrencyGovernor
            DagConcurrencyGovernor.reset_instance()
        except ImportError:
            pass
            
        # Simulate 3 concurrent ticks (as if multiple worker tasks are running)
        mission1 = {"mission_id": "m1", "payload_json": "{}"}
        mission2 = {"mission_id": "m2", "payload_json": "{}"}
        mission3 = {"mission_id": "m3", "payload_json": "{}"}
        
        # Run them concurrently
        await asyncio.gather(
            loop_obj._execute_mission_logic(mission1, "m1", {}),
            loop_obj._execute_mission_logic(mission2, "m2", {}),
            loop_obj._execute_mission_logic(mission3, "m3", {})
        )
        
        # Max concurrency should not exceed 2
        assert max_observed_concurrency <= 2, f"Expected max concurrency 2, got {max_observed_concurrency}"
