
import base64
import hashlib
import hmac
import json
import asyncio
import time
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from universal_agent.hooks_service import (
    HookAction,
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
    
    assert response.status_code == 200
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
    
    assert response.status_code == 200
    
    await asyncio.sleep(0.1)
    
    mock_gateway.resume_session.assert_called_once_with("session_hook_test-session")
    mock_gateway.create_session.assert_called_once_with(
        user_id="webhook",
        workspace_dir="AGENT_RUN_WORKSPACES/session_hook_test-session",
    )
    mock_gateway.execute.assert_called()
    assert mock_gateway.execute.call_args[0][0] == mock_session


@pytest.mark.asyncio
async def test_action_timeout_logs_and_stops_dispatch(hooks_service, mock_gateway, caplog):
    hooks_service._youtube_ingest_mode = ""
    mock_session = GatewaySession(
        session_id="session_hook_timeout-session",
        user_id="webhook",
        workspace_dir="/tmp",
    )
    mock_gateway.resume_session = AsyncMock(return_value=mock_session)

    async def slow_gen(*_args, **_kwargs):
        await asyncio.sleep(5)
        if False:
            yield "never"

    mock_gateway.execute.side_effect = slow_gen

    action = HookAction(
        kind="agent",
        message="slow run",
        name="TimeoutHook",
        session_key="timeout-session",
        timeout_seconds=1,
    )

    with caplog.at_level("ERROR"):
        await hooks_service._dispatch_action(action)

    assert "Hook action timed out" in caplog.text


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
    assert response.status_code == 200

    await asyncio.sleep(0.1)
    gateway_request = mock_gateway.execute.call_args[0][1]
    assert "Task(subagent_type='youtube-explainer-expert'" in gateway_request.user_input
    assert "Resolved artifacts root (absolute):" in gateway_request.user_input
    assert "never use a literal UA_ARTIFACTS_DIR folder name" in gateway_request.user_input
    assert "degraded_transcript_only or failed" in gateway_request.user_input
    assert "Webhook payload values below are authoritative for this run." in gateway_request.user_input
    assert "authoritative_video_url: https://www.youtube.com/watch?v=abc" in gateway_request.user_input
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
    assert response.status_code == 200

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
async def test_dispatch_internal_payload_agent_dispatch(hooks_service):
    hooks_service.config.enabled = True
    hooks_service.config.mappings = [
        HookMappingConfig(
            id="internal-agent",
            match=HookMatchConfig(path="youtube/manual"),
            action="agent",
            message_template="video={{ payload.video_url }}",
            name="InternalHook",
            session_key="internal-session",
        )
    ]
    hooks_service._dispatch_action = AsyncMock(return_value=None)

    ok, reason = await hooks_service.dispatch_internal_payload(
        subpath="youtube/manual",
        payload={"video_url": "https://www.youtube.com/watch?v=abc123xyz00"},
        headers={"x-internal": "1"},
    )
    assert ok is True
    assert reason == "agent"
    await asyncio.sleep(0.05)
    hooks_service._dispatch_action.assert_called_once()
    action = hooks_service._dispatch_action.call_args[0][0]
    assert action.kind == "agent"
    assert "video=https://www.youtube.com/watch?v=abc123xyz00" in (action.message or "")


@pytest.mark.asyncio
async def test_dispatch_internal_payload_skipped_when_transform_returns_none(hooks_service):
    hooks_service.config.enabled = True
    hooks_service.config.mappings = [
        HookMappingConfig(
            id="internal-skip",
            match=HookMatchConfig(path="youtube/manual"),
            action="agent",
            message_template="ignored",
            name="InternalSkip",
            session_key="internal-skip-session",
        )
    ]
    hooks_service._build_action = AsyncMock(return_value=None)
    hooks_service._dispatch_action = AsyncMock(return_value=None)

    ok, reason = await hooks_service.dispatch_internal_payload(
        subpath="youtube/manual",
        payload={"video_url": "https://www.youtube.com/watch?v=abc"},
    )
    assert ok is True
    assert reason == "skipped"
    hooks_service._dispatch_action.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_internal_payload_no_match(hooks_service):
    hooks_service.config.enabled = True
    hooks_service.config.mappings = [
        HookMappingConfig(
            id="internal-unmatched",
            match=HookMatchConfig(path="youtube/manual"),
            action="agent",
            message_template="x",
            name="InternalNoMatch",
            session_key="internal-no-match",
        )
    ]
    hooks_service._dispatch_action = AsyncMock(return_value=None)

    ok, reason = await hooks_service.dispatch_internal_payload(
        subpath="youtube/other",
        payload={"video_url": "https://www.youtube.com/watch?v=abc"},
    )
    assert ok is False
    assert reason == "no_match"
    hooks_service._dispatch_action.assert_not_called()

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

    assert response.status_code == 200

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

    assert first.status_code == 200
    assert second.status_code == 200
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
    assert response.status_code == 200


def test_auto_bootstrap_enables_youtube_mappings_when_config_missing(mock_gateway, tmp_path):
    config_dir = tmp_path / "runtime"
    config_dir.mkdir(parents=True, exist_ok=True)
    ops_path = config_dir / "ops_config.json"
    transforms_dir = tmp_path / "webhook_transforms"
    transforms_dir.mkdir(parents=True, exist_ok=True)
    (transforms_dir / "composio_youtube_transform.py").write_text("def transform(_):\n    return {}\n", encoding="utf-8")
    (transforms_dir / "manual_youtube_transform.py").write_text("def transform(_):\n    return {}\n", encoding="utf-8")

    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch("universal_agent.hooks_service.resolve_ops_config_path", return_value=ops_path),
        patch.dict(
            "os.environ",
            {"UA_HOOKS_TOKEN": "token-123", "COMPOSIO_WEBHOOK_SECRET": "secret-123"},
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)

    mapping_ids = {mapping.id for mapping in service.config.mappings}
    assert service.config.enabled is True
    assert "youtube-manual-url" in mapping_ids
    assert "composio-youtube-trigger" in mapping_ids


def test_auto_bootstrap_does_not_add_manual_mapping_without_token(mock_gateway, tmp_path):
    config_dir = tmp_path / "runtime"
    config_dir.mkdir(parents=True, exist_ok=True)
    ops_path = config_dir / "ops_config.json"
    transforms_dir = tmp_path / "webhook_transforms"
    transforms_dir.mkdir(parents=True, exist_ok=True)
    (transforms_dir / "composio_youtube_transform.py").write_text("def transform(_):\n    return {}\n", encoding="utf-8")
    (transforms_dir / "manual_youtube_transform.py").write_text("def transform(_):\n    return {}\n", encoding="utf-8")

    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch("universal_agent.hooks_service.resolve_ops_config_path", return_value=ops_path),
        patch.dict("os.environ", {"COMPOSIO_WEBHOOK_SECRET": "secret-123"}, clear=True),
    ):
        service = HooksService(mock_gateway)

    mapping_ids = {mapping.id for mapping in service.config.mappings}
    assert service.config.enabled is True
    assert "composio-youtube-trigger" in mapping_ids
    assert "youtube-manual-url" not in mapping_ids


@pytest.mark.asyncio
async def test_local_ingest_success_injects_transcript_metadata(mock_gateway, tmp_path):
    config = HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="route-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template="video_url: https://www.youtube.com/watch?v=dxlyCPGCvy8\nvideo_id: dxlyCPGCvy8",
                name="RouteHook",
                session_key="yt_route_dxlyCPGCvy8",
                to="youtube-explainer-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_dxlyCPGCvy8"
    session = GatewaySession(
        session_id="session_hook_yt_route_dxlyCPGCvy8",
        user_id="webhook",
        workspace_dir=str(workspace_dir),
    )
    mock_gateway.resume_session = AsyncMock(return_value=session)

    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker",
                "UA_HOOKS_YOUTUBE_INGEST_URL": "http://127.0.0.1:18002/api/v1/youtube/ingest",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "1",
                "UA_HOOKS_YOUTUBE_INGEST_MIN_CHARS": "333",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
        service.config = config
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": True,
                "status": "succeeded",
                "source": "youtube_transcript_api",
                "transcript_text": "hello world transcript",
                "transcript_chars": 22,
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    gateway_request = mock_gateway.execute.call_args[0][1]
    assert "local_youtube_ingest_status: succeeded" in gateway_request.user_input
    assert gateway_request.metadata["hook_youtube_ingest_status"] == "succeeded"
    transcript_file = Path(gateway_request.metadata["hook_youtube_ingest_transcript_file"])
    assert transcript_file.exists()
    assert transcript_file.read_text(encoding="utf-8") == "hello world transcript"
    assert service._call_local_youtube_ingest_worker.await_args.kwargs["min_chars"] == 333


@pytest.mark.asyncio
async def test_local_ingest_fail_closed_defers_dispatch(mock_gateway, tmp_path):
    config = HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="route-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template="video_url: https://www.youtube.com/watch?v=dxlyCPGCvy8\nvideo_id: dxlyCPGCvy8",
                name="RouteHook",
                session_key="yt_route_dxlyCPGCvy8_fail",
                to="youtube-explainer-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_dxlyCPGCvy8_fail"
    session = GatewaySession(
        session_id="session_hook_yt_route_dxlyCPGCvy8_fail",
        user_id="webhook",
        workspace_dir=str(workspace_dir),
    )
    mock_gateway.resume_session = AsyncMock(return_value=session)

    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker",
                "UA_HOOKS_YOUTUBE_INGEST_URL": "http://127.0.0.1:18002/api/v1/youtube/ingest",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "1",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
        service.config = config
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": False,
                "status": "failed",
                "error": "worker_unavailable",
                "failure_class": "request_blocked",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    mock_gateway.execute.assert_not_called()
    pending_file = workspace_dir / "pending_local_ingest.json"
    assert pending_file.exists()
    payload = json.loads(pending_file.read_text(encoding="utf-8"))
    assert payload["status"] == "failed_local_ingest"
    assert payload["last_result"]["failure_class"] == "request_blocked"


@pytest.mark.asyncio
async def test_local_ingest_cooldown_defers_dispatch(mock_gateway, tmp_path):
    config = HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="route-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template="video_url: https://www.youtube.com/watch?v=dxlyCPGCvy8\nvideo_id: dxlyCPGCvy8",
                name="RouteHook",
                session_key="yt_route_dxlyCPGCvy8_cooldown",
                to="youtube-explainer-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_dxlyCPGCvy8_cooldown"
    session = GatewaySession(
        session_id="session_hook_yt_route_dxlyCPGCvy8_cooldown",
        user_id="webhook",
        workspace_dir=str(workspace_dir),
    )
    mock_gateway.resume_session = AsyncMock(return_value=session)

    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker",
                "UA_HOOKS_YOUTUBE_INGEST_URL": "http://127.0.0.1:18002/api/v1/youtube/ingest",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "1",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
        service.config = config
        service._youtube_ingest_cooldowns["dxlyCPGCvy8"] = {
            "until_epoch": time.time() + 300.0,
            "failure_class": "request_blocked",
            "error": "youtube_transcript_api_failed",
        }

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    mock_gateway.execute.assert_not_called()
    pending_file = workspace_dir / "pending_local_ingest.json"
    assert pending_file.exists()
    payload = json.loads(pending_file.read_text(encoding="utf-8"))
    assert payload["status"] == "failed_local_ingest"
    assert payload["last_result"]["error"] == "ingest_cooldown_active"
    assert payload["last_result"]["failure_class"] == "request_blocked"


@pytest.mark.asyncio
async def test_local_ingest_failure_emits_notification(mock_gateway, tmp_path):
    config = HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="route-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template="video_url: https://www.youtube.com/watch?v=dxlyCPGCvy8\nvideo_id: dxlyCPGCvy8",
                name="RouteHook",
                session_key="yt_route_dxlyCPGCvy8_notify",
                to="youtube-explainer-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_dxlyCPGCvy8_notify"
    session = GatewaySession(
        session_id="session_hook_yt_route_dxlyCPGCvy8_notify",
        user_id="webhook",
        workspace_dir=str(workspace_dir),
    )
    mock_gateway.resume_session = AsyncMock(return_value=session)
    notifications: list[dict] = []

    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker",
                "UA_HOOKS_YOUTUBE_INGEST_URL": "http://127.0.0.1:18002/api/v1/youtube/ingest",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "2",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS": "0",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway, notification_sink=notifications.append)
        service.config = config
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": False,
                "status": "failed",
                "error": "youtube_transcript_api_failed",
                "failure_class": "request_blocked",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    mock_gateway.execute.assert_not_called()
    assert notifications
    notification = notifications[-1]
    assert notification["kind"] == "youtube_ingest_failed"
    assert notification["severity"] == "error"
    assert "failed after 2/2 attempts" in notification["message"]
    assert notification["metadata"]["failure_class"] == "request_blocked"
    assert notification["metadata"]["attempts"] == 2
    assert notification["metadata"]["max_attempts"] == 2


def test_youtube_ingest_retry_attempts_capped_at_ten(mock_gateway):
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {"UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "50"},
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
    assert service._youtube_ingest_retries == 10


@pytest.mark.asyncio
async def test_dispatch_queue_overflow_emits_notification(mock_gateway):
    config = HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="overflow-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template="hello",
                name="OverflowHook",
                session_key="overflow-session",
            )
        ],
    )
    session = GatewaySession(
        session_id="session_hook_overflow-session",
        user_id="webhook",
        workspace_dir="/tmp",
    )
    mock_gateway.resume_session = AsyncMock(return_value=session)
    notifications: list[dict] = []

    async def slow_gen(*_args, **_kwargs):
        await asyncio.sleep(0.4)
        if False:
            yield "never"

    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_HOOKS_AGENT_DISPATCH_CONCURRENCY": "1",
                "UA_HOOKS_AGENT_DISPATCH_QUEUE_LIMIT": "1",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway, notification_sink=notifications.append)
        service.config = config
        mock_gateway.execute.side_effect = slow_gen

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        first = await service.handle_request(request, "test")
        second = await service.handle_request(request, "test")
        assert first.status_code == 200
        assert second.status_code == 200

        await asyncio.sleep(0.2)

    assert any(item.get("kind") == "hook_dispatch_queue_overflow" for item in notifications)
    # One dispatch should execute, the overflow dispatch should be dropped.
    assert mock_gateway.execute.call_count == 1


@pytest.mark.asyncio
async def test_recover_interrupted_youtube_sessions_queues_recovery(mock_gateway, tmp_path):
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_HOOKS_STARTUP_RECOVERY_ENABLED": "1",
                "UA_HOOKS_STARTUP_RECOVERY_MAX_SESSIONS": "5",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)

    session_id = "session_hook_yt_UCLIo-9WnXvQcXfXomQvYSOg_km5fvKPRsJw"
    session_dir = tmp_path / session_id
    turns_dir = session_dir / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)
    turn_file = turns_dir / "turn_1.jsonl"
    turn_file.write_text(
        json.dumps(
            {
                "event": "turn_started",
                "timestamp": "2026-01-01T00:00:00Z",
                "turn_id": "turn_1",
                "session_id": session_id,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    service._dispatch_action = AsyncMock(return_value=None)
    recovered = await service.recover_interrupted_youtube_sessions(tmp_path)
    assert recovered == 1
    await asyncio.sleep(0.05)
    service._dispatch_action.assert_called_once()
    action = service._dispatch_action.call_args.args[0]
    assert action.to == "youtube-explainer-expert"
    assert "km5fvKPRsJw" in (action.message or "")
    marker = session_dir / ".hook_startup_recovery.json"
    assert marker.exists()


def test_validate_youtube_tutorial_artifacts_allows_concept_only_without_implementation(
    mock_gateway,
    tmp_path,
):
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)

    run_dir = (
        tmp_path
        / "youtube-tutorial-learning"
        / "2026-02-25"
        / "concept-only-video__010101"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "README.md").write_text("# Readme\n", encoding="utf-8")
    (run_dir / "CONCEPT.md").write_text("# Concept\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": "abc123",
                "title": "Concept Only",
                "mode": "explainer_only",
                "learning_mode": "concept_only",
                "status": "completed",
                "artifacts": {
                    "readme": "README.md",
                    "concept": "CONCEPT.md",
                    "implementation_dir": None,
                },
            }
        ),
        encoding="utf-8",
    )

    with patch("universal_agent.hooks_service.resolve_artifacts_dir", return_value=tmp_path):
        result = service._validate_youtube_tutorial_artifacts(
            video_id="abc123",
            started_at_epoch=time.time(),
        )

    assert result["video_id"] == "abc123"
    assert result["implementation_required"] is False
    assert result["status"] == "completed"


def test_validate_youtube_tutorial_artifacts_requires_implementation_for_code_mode(
    mock_gateway,
    tmp_path,
):
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)

    run_dir = (
        tmp_path
        / "youtube-tutorial-learning"
        / "2026-02-25"
        / "code-video__020202"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "README.md").write_text("# Readme\n", encoding="utf-8")
    (run_dir / "CONCEPT.md").write_text("# Concept\n", encoding="utf-8")
    (run_dir / "IMPLEMENTATION.md").write_text("# Impl\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": "code456",
                "title": "Code Mode",
                "mode": "explainer_plus_code",
                "learning_mode": "concept_plus_implementation",
                "status": "full",
                "artifacts": {
                    "readme": "README.md",
                    "concept": "CONCEPT.md",
                    "implementation": "IMPLEMENTATION.md",
                    "implementation_dir": "implementation/",
                },
            }
        ),
        encoding="utf-8",
    )

    with patch("universal_agent.hooks_service.resolve_artifacts_dir", return_value=tmp_path):
        with pytest.raises(RuntimeError, match="youtube_artifacts_incomplete:implementation/"):
            service._validate_youtube_tutorial_artifacts(
                video_id="code456",
                started_at_epoch=time.time(),
            )
