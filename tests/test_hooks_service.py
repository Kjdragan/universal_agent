
import base64
import hashlib
import hmac
import json
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from universal_agent.hooks_service import (
    HookAuthConfig,
    HookMappingConfig,
    HookMatchConfig,
    HooksConfig,
    HooksService,
    HookTransformConfig,
)
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
    request.body = AsyncMock(return_value=b"{}")
    request.query_params = {}
    
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
    mock_gateway.resume_session.assert_called_once_with("session_hook_test-session")
    mock_gateway.create_session.assert_not_called()

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
    
    mock_gateway.resume_session.assert_called_once_with("session_hook_test-session")
    mock_gateway.create_session.assert_called_once_with(
        user_id="webhook",
        workspace_dir="AGENT_RUN_WORKSPACES/session_hook_test-session",
    )
    mock_gateway.execute.assert_called()
    assert mock_gateway.execute.call_args[0][0] == mock_session


@pytest.mark.asyncio
async def test_action_to_injects_routing_prompt_and_metadata(hooks_service, mock_gateway):
    hooks_service.config.mappings = [
        HookMappingConfig(
            id="route-hook",
            match=HookMatchConfig(path="test"),
            action="agent",
            message_template="video_url: https://www.youtube.com/watch?v=abc",
            name="RouteHook",
            session_key="yt_route_abc",
            to="youtube-explainer-expert",
        )
    ]
    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer secret-token"}
    request.body = AsyncMock(return_value=b"{}")
    request.query_params = {}

    mock_session = GatewaySession(session_id="session_hook_yt_route_abc", user_id="webhook", workspace_dir="/tmp")
    mock_gateway.resume_session = AsyncMock(return_value=mock_session)

    response = await hooks_service.handle_request(request, "test")
    assert response.status_code == 202

    await asyncio.sleep(0.1)
    gateway_request = mock_gateway.execute.call_args[0][1]
    assert "Task(subagent_type='youtube-explainer-expert'" in gateway_request.user_input
    assert gateway_request.metadata["hook_route_to"] == "youtube-explainer-expert"

@pytest.mark.asyncio
async def test_no_match(hooks_service):
    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer secret-token"}
    request.body = AsyncMock(return_value=b'{}')
    request.query_params = {}
    
    response = await hooks_service.handle_request(request, "nomatch")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_header_match_success(hooks_service):
    hooks_service.config.mappings = [
        HookMappingConfig(
            id="header-hook",
            match=HookMatchConfig(path="test", headers={"x-event-type": "push"}),
            action="wake",
            text_template="ok",
        )
    ]
    request = MagicMock(spec=Request)
    request.headers = {
        "Authorization": "Bearer secret-token",
        "X-Event-Type": "push",
    }
    request.body = AsyncMock(return_value=b'{}')
    request.query_params = {}

    response = await hooks_service.handle_request(request, "test")
    assert response.status_code == 202

@pytest.mark.asyncio
async def test_header_match_failure(hooks_service):
    hooks_service.config.mappings = [
        HookMappingConfig(
            id="header-hook",
            match=HookMatchConfig(path="test", headers={"x-event-type": "push"}),
            action="wake",
            text_template="ok",
        )
    ]
    request = MagicMock(spec=Request)
    request.headers = {
        "Authorization": "Bearer secret-token",
        "X-Event-Type": "pull_request",
    }
    request.body = AsyncMock(return_value=b'{}')
    request.query_params = {}

    response = await hooks_service.handle_request(request, "test")
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

def _composio_signature(secret: str, webhook_id: str, webhook_timestamp: str, body: bytes) -> str:
    signing_string = f"{webhook_id}.{webhook_timestamp}.{body.decode('utf-8')}"
    digest = hmac.new(secret.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode('utf-8')}"

@pytest.mark.asyncio
async def test_composio_hmac_valid(mock_gateway):
    config = HooksConfig(
        enabled=True,
        token=None,
        mappings=[
            HookMappingConfig(
                id="composio",
                match=HookMatchConfig(path="composio"),
                auth=HookAuthConfig(strategy="composio_hmac"),
                action="wake",
                text_template="ok",
            )
        ],
    )
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)
        service.config = config

    secret = "test-composio-secret"
    webhook_id = "msg_123"
    webhook_timestamp = str(int(time.time()))
    body = b'{"type":"composio.trigger.message","data":{"x":1}}'

    request = MagicMock(spec=Request)
    request.headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": webhook_timestamp,
        "webhook-signature": _composio_signature(secret, webhook_id, webhook_timestamp, body),
    }
    request.body = AsyncMock(return_value=body)
    request.query_params = {}

    with patch.dict("os.environ", {"COMPOSIO_WEBHOOK_SECRET": secret}, clear=False):
        response = await service.handle_request(request, "composio")

    assert response.status_code == 202

@pytest.mark.asyncio
async def test_composio_hmac_invalid_signature(mock_gateway):
    config = HooksConfig(
        enabled=True,
        token=None,
        mappings=[
            HookMappingConfig(
                id="composio",
                match=HookMatchConfig(path="composio"),
                auth=HookAuthConfig(strategy="composio_hmac"),
                action="wake",
                text_template="ok",
            )
        ],
    )
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)
        service.config = config

    body = b'{"type":"composio.trigger.message","data":{"x":1}}'
    request = MagicMock(spec=Request)
    request.headers = {
        "webhook-id": "msg_123",
        "webhook-timestamp": str(int(time.time())),
        "webhook-signature": "v1,not-valid",
    }
    request.body = AsyncMock(return_value=body)
    request.query_params = {}

    with patch.dict("os.environ", {"COMPOSIO_WEBHOOK_SECRET": "test-composio-secret"}, clear=False):
        response = await service.handle_request(request, "composio")

    assert response.status_code == 401

@pytest.mark.asyncio
async def test_composio_hmac_replay_rejected(mock_gateway):
    config = HooksConfig(
        enabled=True,
        token=None,
        mappings=[
            HookMappingConfig(
                id="composio",
                match=HookMatchConfig(path="composio"),
                auth=HookAuthConfig(strategy="composio_hmac", replay_window_seconds=600),
                action="wake",
                text_template="ok",
            )
        ],
    )
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)
        service.config = config

    secret = "test-composio-secret"
    webhook_id = "msg_abc"
    webhook_timestamp = str(int(time.time()))
    body = b'{"type":"composio.trigger.message","data":{"x":1}}'
    signature = _composio_signature(secret, webhook_id, webhook_timestamp, body)

    request1 = MagicMock(spec=Request)
    request1.headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": webhook_timestamp,
        "webhook-signature": signature,
    }
    request1.body = AsyncMock(return_value=body)
    request1.query_params = {}

    request2 = MagicMock(spec=Request)
    request2.headers = dict(request1.headers)
    request2.body = AsyncMock(return_value=body)
    request2.query_params = {}

    with patch.dict("os.environ", {"COMPOSIO_WEBHOOK_SECRET": secret}, clear=False):
        first = await service.handle_request(request1, "composio")
        second = await service.handle_request(request2, "composio")

    assert first.status_code == 202
    assert second.status_code == 202
    assert json.loads(second.body)["deduped"] is True

@pytest.mark.asyncio
async def test_composio_hmac_timestamp_skew_rejected(mock_gateway):
    config = HooksConfig(
        enabled=True,
        token=None,
        mappings=[
            HookMappingConfig(
                id="composio",
                match=HookMatchConfig(path="composio"),
                auth=HookAuthConfig(strategy="composio_hmac", timestamp_tolerance_seconds=60),
                action="wake",
                text_template="ok",
            )
        ],
    )
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)
        service.config = config

    secret = "test-composio-secret"
    webhook_id = "msg_old"
    webhook_timestamp = str(int(time.time()) - 3600)
    body = b'{"type":"composio.trigger.message","data":{"x":1}}'
    signature = _composio_signature(secret, webhook_id, webhook_timestamp, body)

    request = MagicMock(spec=Request)
    request.headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": webhook_timestamp,
        "webhook-signature": signature,
    }
    request.body = AsyncMock(return_value=body)
    request.query_params = {}

    with patch.dict("os.environ", {"COMPOSIO_WEBHOOK_SECRET": secret}, clear=False):
        response = await service.handle_request(request, "composio")

    assert response.status_code == 401

@pytest.mark.asyncio
async def test_token_strategy_allows_open_mapping_when_token_not_configured(mock_gateway):
    config = HooksConfig(
        enabled=True,
        token=None,
        mappings=[
            HookMappingConfig(
                id="open",
                match=HookMatchConfig(path="open"),
                auth=HookAuthConfig(strategy="token"),
                action="wake",
                text_template="ok",
            )
        ],
    )
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)
        service.config = config

    request = MagicMock(spec=Request)
    request.headers = {}
    request.body = AsyncMock(return_value=b"{}")
    request.query_params = {}

    response = await service.handle_request(request, "open")
    assert response.status_code == 202
