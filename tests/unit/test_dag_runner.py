import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from universal_agent.services.dag_runner import DagRunner, DagState

@pytest.mark.asyncio
async def test_dag_runner_sequential_execution():
    """Test that the DAG runner executes sequential steps."""
    workflow_def = {
        "nodes": [
            {"id": "plan", "type": "action"},
            {"id": "implement", "type": "action"},
            {"id": "validate", "type": "action"}
        ],
        "edges": [
            {"from": "plan", "to": "implement"},
            {"from": "implement", "to": "validate"}
        ],
        "start": "plan"
    }

    mock_action_handler = AsyncMock(return_value={"status": "success"})
    
    runner = DagRunner(workflow_def)
    runner.register_handler("action", mock_action_handler)
    
    state = await runner.run()
    
    assert state.status == "completed"
    assert state.current_node is None
    assert mock_action_handler.call_count == 3
    # Called in order
    assert [call[0][0]["id"] for call in mock_action_handler.call_args_list] == ["plan", "implement", "validate"]

@pytest.mark.asyncio
async def test_dag_runner_human_approval_gate():
    """Test that the DAG runner halts on a WAITING_ON_HUMAN gate."""
    workflow_def = {
        "nodes": [
            {"id": "draft", "type": "action"},
            {"id": "approval", "type": "human_gate"},
            {"id": "publish", "type": "action"}
        ],
        "edges": [
            {"from": "draft", "to": "approval"},
            {"from": "approval", "to": "publish"}
        ],
        "start": "draft"
    }

    mock_action_handler = AsyncMock(return_value={"status": "success"})
    
    runner = DagRunner(workflow_def)
    runner.register_handler("action", mock_action_handler)
    
    state = await runner.run()
    
    assert state.status == "waiting_on_human"
    assert state.current_node == "approval"
    assert mock_action_handler.call_count == 1
    assert mock_action_handler.call_args_list[0][0][0]["id"] == "draft"

@pytest.mark.asyncio
async def test_dag_runner_binary_llm_classifier():
    """Test that a binary LLM classifier can branch the DAG execution deterministically."""
    workflow_def = {
        "nodes": [
            {"id": "run_tests", "type": "action"},
            {"id": "check_results", "type": "llm_binary_classifier", "prompt": "Did the tests pass?"},
            {"id": "fix_code", "type": "action"},
            {"id": "done", "type": "action"}
        ],
        "edges": [
            {"from": "run_tests", "to": "check_results"},
            {"from": "check_results", "to": "done", "condition": "true"},
            {"from": "check_results", "to": "fix_code", "condition": "false"},
            {"from": "fix_code", "to": "run_tests"}
        ],
        "start": "run_tests"
    }

    mock_action_handler = AsyncMock(return_value={"status": "success"})
    
    # We mock the LLM binary classifier to return "false" the first time (tests failed), then "true" (tests passed)
    mock_llm_handler = AsyncMock(side_effect=[{"status": "success", "result": "false"}, {"status": "success", "result": "true"}])
    
    runner = DagRunner(workflow_def)
    runner.register_handler("action", mock_action_handler)
    runner.register_handler("llm_binary_classifier", mock_llm_handler)
    
    state = await runner.run()
    
    assert state.status == "completed"
    
    # Action sequence: run_tests -> fix_code -> run_tests -> done
    assert mock_action_handler.call_count == 4
    assert [call[0][0]["id"] for call in mock_action_handler.call_args_list] == ["run_tests", "fix_code", "run_tests", "done"]
    
    # LLM called twice
    assert mock_llm_handler.call_count == 2


@pytest.mark.asyncio
async def test_dag_runner_max_iterations():
    """Test that the DAG runner terminates runaway loops at the max iteration limit."""
    workflow_def = {
        "nodes": [
            {"id": "step_a", "type": "action"},
            {"id": "step_b", "type": "action"},
        ],
        "edges": [
            {"from": "step_a", "to": "step_b"},
            {"from": "step_b", "to": "step_a"},  # infinite loop
        ],
        "start": "step_a"
    }

    mock_action_handler = AsyncMock(return_value={"status": "success"})

    runner = DagRunner(workflow_def, max_iterations=10)
    runner.register_handler("action", mock_action_handler)

    state = await runner.run()

    assert state.status == "failed"
    assert "max iterations" in state.context.get("error", "").lower()
    assert mock_action_handler.call_count == 10


@pytest.mark.asyncio
async def test_dag_runner_execution_history():
    """Test that the DAG runner records an ordered execution history for audit."""
    workflow_def = {
        "nodes": [
            {"id": "plan", "type": "action"},
            {"id": "implement", "type": "action"},
            {"id": "validate", "type": "action"}
        ],
        "edges": [
            {"from": "plan", "to": "implement"},
            {"from": "implement", "to": "validate"}
        ],
        "start": "plan"
    }

    mock_action_handler = AsyncMock(return_value={"status": "success"})

    runner = DagRunner(workflow_def)
    runner.register_handler("action", mock_action_handler)

    state = await runner.run()

    assert state.status == "completed"
    assert hasattr(state, "history")
    assert len(state.history) == 3
    assert [entry["node_id"] for entry in state.history] == ["plan", "implement", "validate"]
    # Each entry should have a node_id and status
    for entry in state.history:
        assert "node_id" in entry
        assert "status" in entry
