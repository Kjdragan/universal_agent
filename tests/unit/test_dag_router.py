import pytest
import json
from unittest.mock import MagicMock, patch

from universal_agent.vp.clients.dag_client import DagClient


def test_select_client_dag_mode():
    """Test that _select_client_for_mission returns DagClient for dag execution mode."""
    from universal_agent.vp.worker_loop import VpWorkerLoop

    # We need to construct a minimal VpWorkerLoop to test the method.
    # Mock the dependencies heavily.
    with patch("universal_agent.vp.worker_loop.VpWorkerLoop.__init__", return_value=None):
        loop = VpWorkerLoop.__new__(VpWorkerLoop)
        loop._client = None  # No pre-set client
        loop.vp_id = "vp.coder.primary"

        mission = {
            "mission_id": "test-dag-001",
            "payload_json": json.dumps({
                "execution_mode": "dag",
                "dag_definition": {"nodes": [], "edges": [], "start": "x"},
            }),
        }

        # Mock mission to behave like a dict (sqlite3.Row has .keys())
        class FakeRow(dict):
            def keys(self):
                return list(super().keys())

        client = loop._select_client_for_mission(FakeRow(mission))
        assert isinstance(client, DagClient)


def test_select_client_default_unchanged():
    """Test that existing sdk/default mode still returns the default client."""
    from universal_agent.vp.worker_loop import VpWorkerLoop

    with patch("universal_agent.vp.worker_loop.VpWorkerLoop.__init__", return_value=None):
        loop = VpWorkerLoop.__new__(VpWorkerLoop)
        loop._client = None
        loop._default_client = MagicMock()
        loop.vp_id = "vp.coder.primary"

        mission = {
            "mission_id": "test-sdk-001",
            "payload_json": json.dumps({
                "execution_mode": "sdk",
            }),
        }

        class FakeRow(dict):
            def keys(self):
                return list(super().keys())

        client = loop._select_client_for_mission(FakeRow(mission))
        assert client is loop._default_client
