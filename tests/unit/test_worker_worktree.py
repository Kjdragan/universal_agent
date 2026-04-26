import pytest
import sqlite3
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from universal_agent.vp.worker_loop import VpWorkerLoop
from universal_agent.vp.profiles import VpProfile

@pytest.mark.asyncio
async def test_vp_coder_provisions_git_worktree(tmp_path: Path):
    """
    Verify that when vp.coder.primary executes a mission, it provisions
    a git worktree instead of just returning a plain directory path.
    """
    # 1. Setup mock profile and worker loop
    profile = VpProfile(
        vp_id="vp.coder.primary",
        display_name="CODIE",
        runtime_id="runtime.coder.external",
        client_kind="claude_code",
        workspace_root=tmp_path / "workspaces",
    )
    
    conn = MagicMock(spec=sqlite3.Connection)
    
    # We patch get_vp_profile so VpWorkerLoop.__init__ succeeds
    with patch("universal_agent.vp.worker_loop.get_vp_profile", return_value=profile):
        loop = VpWorkerLoop(
            conn=conn,
            vp_id="vp.coder.primary",
            worker_id="worker-123",
            workspace_base=tmp_path
        )
    
    # 2. Setup mock mission
    repo_path = tmp_path / "main_repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").mkdir(parents=True, exist_ok=True)
    
    mission = {
        "mission_id": "test-mission-123",
        "payload_json": json.dumps({
            "objective": "Test objective",
            "constraints": {"target_path": str(repo_path)}
        })
    }
    
    # 3. Patch subprocess.run to intercept git worktree command
    with patch("universal_agent.vp.worker_loop.subprocess.run") as mock_run:
        # For this test, we assume there's a new method _provision_workspace
        # or we call it through _resolve_mission_workspace
        # The exact implementation will make this pass.
        
        # We will test _provision_workspace which we expect to create the worktree
        # and return the Path to it.
        try:
            workspace_dir = await loop._provision_workspace(mission)
        except AttributeError:
            pytest.fail("VpWorkerLoop._provision_workspace method not implemented yet (Red Phase)")

        # Verify git worktree add was called
        calls = mock_run.call_args_list
        worktree_called = False
        for call in calls:
            args = call[0][0]
            if args[0] == "git" and args[1] == "worktree" and args[2] == "add":
                worktree_called = True
                assert str(workspace_dir) in args
                break
                
        assert worktree_called, "Expected 'git worktree add' to be called during provisioning"
