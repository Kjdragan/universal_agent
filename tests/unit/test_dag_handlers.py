import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from universal_agent.services.dag_runner import DagState
from universal_agent.services.dag_handlers import (
    subprocess_handler,
    make_subprocess_handler,
    make_llm_binary_classifier_handler,
)


@pytest.mark.asyncio
async def test_subprocess_handler_success():
    """Test that the subprocess handler runs a command and captures output."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        handler = make_subprocess_handler(workspace_root=Path(tmpdir))
        node = {"id": "echo_test", "type": "subprocess", "command": "echo hello world"}
        state = DagState(status="running", current_node="echo_test")

        result = await handler(node, state)

        assert result["status"] == "success"
        assert "hello world" in result["context_update"]["stdout"]
        assert result["context_update"]["exit_code"] == 0


@pytest.mark.asyncio
async def test_subprocess_handler_failure():
    """Test that the subprocess handler reports failed commands."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        handler = make_subprocess_handler(workspace_root=Path(tmpdir))
        node = {"id": "bad_cmd", "type": "subprocess", "command": "false"}
        state = DagState(status="running", current_node="bad_cmd")

        result = await handler(node, state)

        assert result["status"] == "failed"
        assert result["context_update"]["exit_code"] != 0


@pytest.mark.asyncio
async def test_llm_binary_classifier_handler_true():
    """Test the binary classifier handler parses 'true' from LLM response."""
    mock_llm_call = AsyncMock(return_value="true")
    handler = make_llm_binary_classifier_handler(llm_call=mock_llm_call)
    node = {
        "id": "check",
        "type": "llm_binary_classifier",
        "prompt": "Did the tests pass?",
    }
    state = DagState(status="running", current_node="check")
    state.context["stdout"] = "All 10 tests passed"

    result = await handler(node, state)

    assert result["status"] == "success"
    assert result["result"] == "true"
    mock_llm_call.assert_called_once()
    # The call should include context and the node prompt
    call_text = mock_llm_call.call_args[0][0]
    assert "Did the tests pass?" in call_text


@pytest.mark.asyncio
async def test_llm_binary_classifier_handler_false():
    """Test the binary classifier handler parses 'false' from LLM response."""
    mock_llm_call = AsyncMock(return_value="False")
    handler = make_llm_binary_classifier_handler(llm_call=mock_llm_call)
    node = {
        "id": "check",
        "type": "llm_binary_classifier",
        "prompt": "Did the tests pass?",
    }
    state = DagState(status="running", current_node="check")
    state.context["stdout"] = "FAILED test_something"

    result = await handler(node, state)

    assert result["status"] == "success"
    assert result["result"] == "false"


@pytest.mark.asyncio
async def test_llm_binary_classifier_handler_ambiguous():
    """Test the binary classifier defaults to 'false' on ambiguous LLM output."""
    mock_llm_call = AsyncMock(return_value="I'm not sure, maybe?")
    handler = make_llm_binary_classifier_handler(llm_call=mock_llm_call)
    node = {
        "id": "check",
        "type": "llm_binary_classifier",
        "prompt": "Did the tests pass?",
    }
    state = DagState(status="running", current_node="check")

    result = await handler(node, state)

    assert result["status"] == "success"
    # Ambiguous response should default to "false" for safety
    assert result["result"] == "false"
