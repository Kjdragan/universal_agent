
import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from fastapi import Request, Response
from universal_agent.hooks_service import HooksService, HooksConfig, HookMappingConfig, HookMatchConfig, HookAction, HookTransformConfig
from universal_agent.gateway import InProcessGateway, GatewaySession

@pytest.fixture
def mock_gateway():
    gateway = MagicMock(spec=InProcessGateway)
    gateway.resume_session = AsyncMock()
    gateway.create_session = AsyncMock()
    gateway.execute = MagicMock()
    
    # Mock execute as an async generator
    async def async_gen(*args, **kwargs):
        yield "event"
    gateway.execute.side_effect = async_gen
    
    return gateway

@pytest.fixture
def mock_config():
    return HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="test-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template="Hello {{ payload.name }}",
                name="TestHook",
                session_key="test-session"
            )
        ]
    )

@pytest.fixture
def hooks_service(mock_gateway, mock_config):
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)
        service.config = mock_config  # Inject mock config directly
        return service

@pytest.mark.asyncio
async def test_disabled_service(hooks_service):
    hooks_service.config.enabled = False
    request = MagicMock(spec=Request)
    
    response = await hooks_service.handle_request(request, "test")
    assert response.status_code == 404
    assert response.body == b"Hooks disabled"

@pytest.mark.asyncio
async def test_unauthorized(hooks_service):
    request = MagicMock(spec=Request)
    request.headers = {}
    
    response = await hooks_service.handle_request(request, "test")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_authorized_success(hooks_service, mock_gateway):
    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer secret-token"}
    request.body = AsyncMock(return_value=b'{"name": "World"}')
    request.query_params = {}

    # Setup gateway mock returns
    mock_session = GatewaySession(session_id="session_test_session", user_id="webhook", workspace_dir="/tmp")
    
    # Configure both resume and create to return the session, just in case logic falls through
    mock_gateway.resume_session = AsyncMock(return_value=mock_session)
    mock_gateway.create_session = AsyncMock(return_value=mock_session)

    response = await hooks_service.handle_request(request, "test")
    
    assert response.status_code == 202
    assert json.loads(response.body)["ok"] is True
    
    # Wait a bit for the background task
    await asyncio.sleep(0.1)
    
    # Verify dispatch
    # One of them should be called. 
    # Since we didn't force raise ValueError, resume_session should be called.
    
    # Check if execute was called with mock_session
    mock_gateway.execute.assert_called()
    call_args = mock_gateway.execute.call_args
    assert call_args[0][0] == mock_session
    gateway_request = call_args[0][1]
    assert gateway_request.user_input == "Hello World"
    assert gateway_request.metadata["source"] == "webhook"

@pytest.mark.asyncio
async def test_authorized_create_new(hooks_service, mock_gateway):
    # Test path where resume fails
    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer secret-token"}
    request.body = AsyncMock(return_value=b'{"name": "NewUser"}')
    request.query_params = {}

    mock_session = GatewaySession(session_id="session_new", user_id="webhook", workspace_dir="/tmp/new")
    
    mock_gateway.resume_session = AsyncMock(side_effect=ValueError("Not found"))
    mock_gateway.create_session = AsyncMock(return_value=mock_session)

    response = await hooks_service.handle_request(request, "test")
    
    assert response.status_code == 202
    
    await asyncio.sleep(0.1)
    
    mock_gateway.create_session.assert_called()
    mock_gateway.execute.assert_called()
    assert mock_gateway.execute.call_args[0][0] == mock_session

@pytest.mark.asyncio
async def test_no_match(hooks_service):
    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer secret-token"}
    request.body = AsyncMock(return_value=b'{}')
    request.query_params = {}
    
    response = await hooks_service.handle_request(request, "nomatch")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_template_rendering(hooks_service):
    context = {
        "payload": {"user": {"name": "Alice"}},
        "headers": {"x-test": "123"}
    }
    template = "User {{ payload.user.name }} sent {{ headers.x-test }}"
    result = hooks_service._render_template(template, context)
    assert result == "User Alice sent 123"

@pytest.mark.asyncio
async def test_transform(hooks_service, tmp_path):
    # Create a dummy transform module
    transform_file = tmp_path / "my_transform.py"
    transform_file.write_text("""
def transform(ctx):
    if ctx['payload'].get('skip'):
        return None
    return {"message": "Transformed " + ctx['payload']['val']}
""")
    
    hooks_service.config.transforms_dir = str(tmp_path)
    
    mapping = HookMappingConfig(
        transform=HookTransformConfig(module="my_transform.py"),
        action="agent"
    )
    
    # Test valid transform
    context = {"payload": {"val": "Data"}, "headers": {}, "path": "t", "query": {}}
    action = await hooks_service._build_action(mapping, context)
    
    assert action is not None
    assert action.message == "Transformed Data"
    
    # Test skip
    context = {"payload": {"skip": True}, "headers": {}, "path": "t", "query": {}}
    action = await hooks_service._build_action(mapping, context)
    
    assert action is None

