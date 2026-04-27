import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent.vp.clients.dag_client import DagClient


@pytest.mark.asyncio
async def test_dag_client_runs_workflow():
    """Test that DagClient executes a DAG workflow and returns a MissionOutcome."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        mission = {
            "mission_id": "test-mission-001",
            "objective": "Run test pipeline",
            "payload_json": json.dumps({
                "dag_definition": {
                    "nodes": [
                        {"id": "step1", "type": "subprocess", "command": "echo step1_done"},
                        {"id": "step2", "type": "subprocess", "command": "echo step2_done"},
                    ],
                    "edges": [
                        {"from": "step1", "to": "step2"},
                    ],
                    "start": "step1",
                },
            }),
        }

        client = DagClient()
        outcome = await client.run_mission(mission=mission, workspace_root=workspace)

        assert outcome.status == "completed"
        assert outcome.result_ref is not None
        # Context holds the *last* node's output; history proves both ran
        assert len(outcome.payload["dag_history"]) == 2
        assert outcome.payload["dag_history"][0]["node_id"] == "step1"
        assert outcome.payload["dag_history"][1]["node_id"] == "step2"


@pytest.mark.asyncio
async def test_dag_client_missing_definition():
    """Test that DagClient fails gracefully when dag_definition is absent."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        mission = {
            "mission_id": "test-mission-002",
            "objective": "Run empty pipeline",
            "payload_json": json.dumps({}),
        }

        client = DagClient()
        outcome = await client.run_mission(mission=mission, workspace_root=workspace)

        assert outcome.status == "failed"
        assert "dag_definition" in (outcome.message or "").lower()


@pytest.mark.asyncio
async def test_dag_client_yaml_file_workflow():
    """Test that DagClient can load a workflow from a YAML file path."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        # Write a workflow YAML file
        yaml_path = workspace / "workflow.yaml"
        yaml_path.write_text("""\
nodes:
  - id: greet
    type: subprocess
    command: "echo hello_from_yaml"
edges: []
start: greet
""")

        mission = {
            "mission_id": "test-mission-003",
            "objective": "Run YAML pipeline",
            "payload_json": json.dumps({
                "dag_definition_path": str(yaml_path),
            }),
        }

        client = DagClient()
        outcome = await client.run_mission(mission=mission, workspace_root=workspace)

        assert outcome.status == "completed"
        assert "hello_from_yaml" in str(outcome.payload)
