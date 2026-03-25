
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
from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import get_run_attempt
from universal_agent.hooks_service import (
    HookAction,
    HookAuthConfig,
    HookMappingConfig,
    HookMatchConfig,
    HooksConfig,
    HooksService,
    HookTransformConfig,
    build_manual_youtube_action,
)
from universal_agent.workflow_admission import WorkflowAdmissionService
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
def hooks_service(mock_gateway, mock_config, tmp_path):
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {"UA_RUNTIME_DB_PATH": runtime_db_path},
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
        service.config = mock_config  # Inject mock config directly
        service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
        return service


def _bind_workflow_runtime_db(service: HooksService, tmp_path: Path) -> str:
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
    return runtime_db_path

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
    mock_gateway.create_session.assert_called_once()
    _, kwargs = mock_gateway.create_session.call_args
    assert kwargs["user_id"] == "webhook"
    assert kwargs["session_id"] == "session_hook_test-session"
    assert str(kwargs["workspace_dir"]).endswith(
        "AGENT_RUN_WORKSPACES/run_session_hook_test-session"
    )
    mock_gateway.execute.assert_called()
    assert mock_gateway.execute.call_args[0][0] == mock_session


@pytest.mark.asyncio
async def test_resolve_or_create_webhook_session_uses_explicit_workspace(hooks_service, mock_gateway):
    mock_session = GatewaySession(session_id="session_new", user_id="webhook", workspace_dir="/tmp/new")
    mock_gateway.resume_session = AsyncMock(side_effect=ValueError("Not found"))
    mock_gateway.create_session = AsyncMock(return_value=mock_session)

    result = await hooks_service._resolve_or_create_webhook_session(
        "session_hook_test-session",
        "/tmp/run_hook_test_session",
    )

    assert result == mock_session
    mock_gateway.resume_session.assert_called_once_with("session_hook_test-session")
    mock_gateway.create_session.assert_called_once_with(
        user_id="webhook",
        workspace_dir="/tmp/run_hook_test_session",
        session_id="session_hook_test-session",
    )


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


def test_emit_heartbeat_investigation_completion_sanitizes_recommendation_text(mock_gateway, tmp_path):
    notifications: list[dict] = []
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway, notification_sink=notifications.append)

    workspace_root = tmp_path / "session_hook_abc"
    work_products = workspace_root / "work_products"
    work_products.mkdir(parents=True)
    (work_products / "heartbeat_investigation_summary.json").write_text(
        json.dumps(
            {
                "source_notification_id": "ntf_123",
                "classification": "infrastructure_access",
                "operator_review_required": True,
                "recommended_next_step": (
                    "Update Tailscale ACL to permit SSH from mint-desktop to srv1360701, "
                    "OR add workstation IP to DigitalOcean firewall, "
                    "OR manually run DLQ replay from VPS console."
                ),
                "email_summary": (
                    "VPS is healthy via Tailscale mesh but SSH is blocked by ACL policy. "
                    "Add workstation IP to the DigitalOcean firewall if public fallback is needed."
                ),
            }
        ),
        encoding="utf-8",
    )
    (work_products / "heartbeat_investigation_summary.md").write_text(
        "Heartbeat investigation summary.",
        encoding="utf-8",
    )

    service._emit_heartbeat_investigation_completion(
        session_id="session_hook_abc",
        session_key="simone_heartbeat_ntf_123",
        workspace_root=workspace_root,
    )

    assert notifications
    metadata = notifications[0]["metadata"]
    assert metadata["recommended_next_step"].startswith("Update Tailscale ACL/SSH policy")
    assert "DigitalOcean" not in metadata["email_summary"]
    assert "VPS host firewall" in metadata["email_summary"]


def test_validate_youtube_tutorial_artifacts_accepts_nested_video_id_manifest(mock_gateway, tmp_path):
    notifications: list[dict] = []
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway, notification_sink=notifications.append)

    artifacts_root = tmp_path / "artifacts"
    run_dir = artifacts_root / "youtube-tutorial-creation" / "2026-03-24" / "nested_video_manifest_case"
    run_dir.mkdir(parents=True)
    (run_dir / "README.md").write_text("readme", encoding="utf-8")
    (run_dir / "CONCEPT.md").write_text("concept", encoding="utf-8")
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "video": {
                    "video_id": "vid_nested_123",
                    "title": "Nested Manifest Title",
                },
                "mode": "explainer_only",
                "learning_mode": "concept_only",
                "artifacts": {
                    "manifest": "manifest.json",
                    "readme": "README.md",
                    "concept": "CONCEPT.md",
                },
            }
        ),
        encoding="utf-8",
    )
    started_at_epoch = manifest_path.stat().st_mtime

    with patch("universal_agent.hooks_service.resolve_artifacts_dir", return_value=artifacts_root):
        result = service._validate_youtube_tutorial_artifacts(
            video_id="vid_nested_123",
            started_at_epoch=started_at_epoch,
        )

    assert result["manifest_path"] == str(manifest_path)
    assert result["title"] == "Nested Manifest Title"
    assert result["status"] == "completed"


def test_emit_youtube_tutorial_ready_notification_mentions_recovery_attempt(mock_gateway):
    notifications: list[dict] = []
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway, notification_sink=notifications.append)

    service._emit_youtube_tutorial_ready_notification(
        session_id="session_hook_yt_test_retry_vid1234567",
        session_key="yt_channel_vid1234567",
        hook_name="PlaylistWatcherYouTubeWebhook",
        expected_video_id="vid1234567",
        ingest_status="succeeded",
        artifact_validation={
            "title": "Recovered Tutorial",
            "status": "completed",
            "run_rel_path": "youtube-tutorial-creation/2026-03-24/recovered_tutorial",
            "key_files": [{"label": "README", "name": "README.md", "rel_path": "README.md"}],
        },
        pending_recovery_payload={"retry_count": 1, "max_retries": 2},
    )

    assert notifications
    notification = notifications[0]
    assert notification["kind"] == "youtube_tutorial_ready"
    assert "attempt 2/3" in notification["message"]
    metadata = notification["metadata"]
    assert metadata["recovered_after_retry"] is True
    assert metadata["attempt_number"] == 2
    assert metadata["total_attempts_allowed"] == 3


def test_action_to_injects_routing_prompt_and_metadata(hooks_service):
    action = HookAction(
        kind="agent",
        name="RouteHook",
        session_key="yt_route_abc",
        to="youtube-expert",
        message="video_url: https://www.youtube.com/watch?v=abc123xyz00",
    )

    user_input = hooks_service._build_agent_user_input(action)
    assert "Task(subagent_type='youtube-expert'" in user_input
    assert "Resolved artifacts root (absolute):" in user_input
    assert "never use a literal UA_ARTIFACTS_DIR folder name" in user_input
    assert "degraded_transcript_only or failed" in user_input
    assert "Webhook payload values below are authoritative for this run." in user_input
    assert "authoritative_video_url: https://www.youtube.com/watch?v=abc123xyz00" in user_input


def test_action_to_legacy_alias_still_injects_youtube_routing(hooks_service):
    action = HookAction(
        kind="agent",
        name="RouteHookLegacy",
        session_key="yt_route_def",
        to="youtube-explainer-expert",
        message="video_url: https://www.youtube.com/watch?v=def123xyz00",
    )

    user_input = hooks_service._build_agent_user_input(action)
    assert "Task(subagent_type='youtube-explainer-expert'" in user_input
    assert "Resolved artifacts root (absolute):" in user_input
    assert "authoritative_video_url: https://www.youtube.com/watch?v=def123xyz00" in user_input


@pytest.mark.asyncio
async def test_youtube_started_notification_includes_title_and_video_id(hooks_service, mock_gateway):
    hooks_service._youtube_ingest_mode = ""
    notifications = []
    hooks_service._notification_sink = notifications.append
    hooks_service._run_gateway_execute_with_watchdogs = AsyncMock(return_value={})
    hooks_service._validate_youtube_tutorial_artifacts = MagicMock(
        return_value={
            "title": "Demo Tutorial",
            "status": "full",
            "run_rel_path": "",
            "key_files": [],
        }
    )

    mock_session = GatewaySession(
        session_id="session_hook_yt_demo123abc",
        user_id="webhook",
        workspace_dir="/tmp",
    )
    mock_gateway.resume_session = AsyncMock(return_value=mock_session)

    action = HookAction(
        kind="agent",
        name="ComposioYouTubeTrigger",
        session_key="yt_demo123abc",
        to="youtube-expert",
        message="\n".join(
            [
                "video_url: https://www.youtube.com/watch?v=demo123abc4",
                "video_id: demo123abc4",
                "title: Building Better Pipelines",
                "mode: explainer_plus_code",
            ]
        ),
    )

    await hooks_service._dispatch_action(action)

    started = next((n for n in notifications if n.get("kind") == "youtube_tutorial_started"), None)
    assert started is not None
    assert "Processing tutorial pipeline attempt 1." in str(started["message"])
    assert started["metadata"]["video_id"] == "demo123abc4"
    assert started["metadata"]["tutorial_title"] == "Building Better Pipelines"


@pytest.mark.asyncio
async def test_youtube_dispatch_interrupted_writes_pending_recovery_marker(hooks_service, mock_gateway, tmp_path):
    hooks_service._youtube_ingest_mode = ""
    hooks_service._schedule_youtube_retry_attempt = MagicMock()
    notifications = []
    hooks_service._notification_sink = notifications.append
    hooks_service._run_gateway_execute_with_watchdogs = AsyncMock(
        return_value={
            "reported_error": True,
            "reported_error_message": "Cannot write to terminated process (exit code: -15)",
        }
    )

    workspace = tmp_path / "session_hook_yt_demo123abc4"
    workspace.mkdir(parents=True, exist_ok=True)
    mock_session = GatewaySession(
        session_id="session_hook_yt_demo123abc4",
        user_id="webhook",
        workspace_dir=str(workspace),
    )
    mock_gateway.resume_session = AsyncMock(return_value=mock_session)

    action = HookAction(
        kind="agent",
        name="ComposioYouTubeTrigger",
        session_key="yt_demo123abc4",
        to="youtube-expert",
        message="\n".join(
            [
                "video_url: https://www.youtube.com/watch?v=demo123abc4",
                "video_id: demo123abc4",
                "title: Interrupted Tutorial",
                "mode: explainer_plus_code",
            ]
        ),
    )

    await hooks_service._dispatch_action(action)

    interrupted = next((n for n in notifications if n.get("kind") == "youtube_tutorial_interrupted"), None)
    assert interrupted is not None
    assert interrupted["metadata"]["reason"] == "hook_dispatch_interrupted"
    assert interrupted["metadata"]["run_id"]
    assert interrupted["metadata"]["attempt_id"]
    assert interrupted["metadata"]["attempt_number"] == 1
    assert not any(n.get("kind") == "youtube_tutorial_failed" for n in notifications)

    conn = connect_runtime_db(hooks_service._workflow_admission_service().db_path)
    ensure_schema(conn)
    attempt_row = get_run_attempt(conn, str(interrupted["metadata"]["attempt_id"]))
    retry_attempt = conn.execute(
        "SELECT * FROM run_attempts WHERE run_id = ? AND attempt_number = 2",
        (str(interrupted["metadata"]["run_id"]),),
    ).fetchone()
    conn.close()
    assert attempt_row is not None
    assert retry_attempt is not None


def test_is_youtube_local_ingest_target_accepts_canonical_and_alias(mock_gateway):
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict("os.environ", {"UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker"}, clear=False),
    ):
        service = HooksService(mock_gateway)

    canonical_action = HookAction(
        kind="agent",
        to="youtube-expert",
        message="video_url: https://www.youtube.com/watch?v=abc123xyz00",
    )
    alias_action = HookAction(
        kind="agent",
        to="youtube-explainer-expert",
        message="video_url: https://www.youtube.com/watch?v=abc123xyz00",
    )

    assert service._is_youtube_local_ingest_target(canonical_action) is True
    assert service._is_youtube_local_ingest_target(alias_action) is True

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
    hooks_service.dispatch_internal_action_background_with_admission = AsyncMock(
        return_value={"decision": "accepted", "reason": "dispatched"}
    )

    ok, reason = await hooks_service.dispatch_internal_payload(
        subpath="youtube/manual",
        payload={"video_url": "https://www.youtube.com/watch?v=abc123xyz00"},
        headers={"x-internal": "1"},
    )
    assert ok is True
    assert reason == "agent"
    hooks_service.dispatch_internal_action_background_with_admission.assert_called_once()
    action_payload = hooks_service.dispatch_internal_action_background_with_admission.call_args[0][0]
    assert action_payload["kind"] == "agent"
    assert "video=https://www.youtube.com/watch?v=abc123xyz00" in str(action_payload.get("message") or "")


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
    hooks_service.dispatch_internal_action_background_with_admission = AsyncMock(
        return_value={"decision": "accepted", "reason": "dispatched"}
    )

    ok, reason = await hooks_service.dispatch_internal_payload(
        subpath="youtube/manual",
        payload={"video_url": "https://www.youtube.com/watch?v=abc"},
    )
    assert ok is True
    assert reason == "skipped"
    hooks_service.dispatch_internal_action_background_with_admission.assert_not_called()


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
    hooks_service.dispatch_internal_action_background_with_admission = AsyncMock(
        return_value={"decision": "accepted", "reason": "dispatched"}
    )

    ok, reason = await hooks_service.dispatch_internal_payload(
        subpath="youtube/other",
        payload={"video_url": "https://www.youtube.com/watch?v=abc"},
    )
    assert ok is False
    assert reason == "no_match"
    hooks_service.dispatch_internal_action_background_with_admission.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_internal_payload_agentmail_route_uses_admitted_background_dispatch(hooks_service, tmp_path):
    runtime_db_path = _bind_workflow_runtime_db(hooks_service, tmp_path)
    hooks_service.config.enabled = True
    hooks_service._dispatch_action = AsyncMock(return_value={"decision": "accepted"})
    hooks_service.config.mappings = [
        HookMappingConfig(
            id="internal-agentmail",
            match=HookMatchConfig(path="gmail/new_message"),
            action="agent",
            message_template="thread_id: {{ payload.thread_id }}\nmessage_id: {{ payload.message_id }}\nsender_email: {{ payload.sender_email }}",
            name="GwsInboundMail",
            session_key="agentmail_{{ payload.thread_id }}",
            to="email-handler",
        )
    ]

    ok, reason = await hooks_service.dispatch_internal_payload(
        subpath="gmail/new_message",
        payload={
            "thread_id": "thd_payload_1",
            "message_id": "msg_payload_1",
            "sender_email": "kevin@example.com",
        },
        headers={"x-ua-source": "gws_event_listener"},
    )

    assert ok is True
    assert reason == "agent"
    await asyncio.sleep(0.05)
    hooks_service._dispatch_action.assert_called()

    conn = connect_runtime_db(runtime_db_path)
    ensure_schema(conn)
    try:
        run_row = conn.execute(
            "SELECT * FROM runs WHERE run_kind = 'agentmail_inbound_hook' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert run_row is not None
    assert str(run_row["trigger_source"] or "") == "agentmail"

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
                to="youtube-expert",
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
        runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
        service = HooksService(mock_gateway)
        service.config = config
        service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
        service._run_gateway_execute_with_watchdogs = AsyncMock(return_value={})
        service._validate_youtube_tutorial_artifacts = MagicMock(
            return_value={"title": "Local Ingest Tutorial", "status": "full", "run_rel_path": "", "key_files": []}
        )
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": True,
                "status": "succeeded",
                "source": "youtube_transcript_api",
                "transcript_text": "hello world transcript",
                "transcript_chars": 22,
                "metadata_status": "attempted_failed",
                "metadata_source": "yt_dlp",
                "metadata_error": "yt_dlp_metadata_failed",
                "metadata_failure_class": "request_blocked",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    gateway_request = service._run_gateway_execute_with_watchdogs.call_args.kwargs["request"]
    assert "local_youtube_ingest_status: succeeded" in gateway_request.user_input
    assert "local_youtube_ingest_metadata_status: attempted_failed" in gateway_request.user_input
    assert gateway_request.metadata["hook_youtube_ingest_status"] == "succeeded"
    assert gateway_request.metadata["hook_youtube_ingest_metadata_status"] == "attempted_failed"
    assert gateway_request.metadata["hook_youtube_ingest_metadata_source"] == "yt_dlp"
    assert gateway_request.metadata["hook_youtube_ingest_metadata_error"] == "yt_dlp_metadata_failed"
    assert gateway_request.metadata["hook_youtube_ingest_metadata_failure_class"] == "request_blocked"
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
                to="youtube-expert",
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
        runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
        service = HooksService(mock_gateway)
        service.config = config
        service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
        service._schedule_youtube_retry_attempt = MagicMock()
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
                to="youtube-expert",
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
        _bind_workflow_runtime_db(service, tmp_path)
        service._schedule_youtube_retry_attempt = MagicMock()
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
                to="youtube-expert",
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
        _bind_workflow_runtime_db(service, tmp_path)
        service._schedule_youtube_retry_attempt = MagicMock()
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


@pytest.mark.asyncio
async def test_local_ingest_non_retryable_failure_stops_after_first_attempt(mock_gateway, tmp_path):
    config = HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="route-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template="video_url: https://www.youtube.com/watch?v=V11KbKnJRmQ\nvideo_id: V11KbKnJRmQ",
                name="RouteHook",
                session_key="yt_route_v11_nonretry",
                to="youtube-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_v11_nonretry"
    session = GatewaySession(
        session_id="session_hook_yt_route_v11_nonretry",
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
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "10",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS": "0",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway, notification_sink=notifications.append)
        service.config = config
        _bind_workflow_runtime_db(service, tmp_path)
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": False,
                "status": "failed",
                "error": "youtube_transcript_api_failed",
                "failure_class": "video_unavailable",
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
    assert service._call_local_youtube_ingest_worker.await_count == 1
    assert notifications
    notification = notifications[-1]
    assert notification["kind"] == "youtube_ingest_failed"
    assert "video is unavailable" in notification["message"]
    assert notification["metadata"]["failure_class"] == "video_unavailable"
    assert notification["metadata"]["attempts"] == 1
    assert notification["metadata"]["max_attempts"] == 10


@pytest.mark.asyncio
async def test_local_ingest_api_unavailable_allow_degraded_fails_open(mock_gateway, tmp_path):
    config = HooksConfig(
        enabled=True,
        token="secret-token",
        mappings=[
            HookMappingConfig(
                id="route-hook",
                match=HookMatchConfig(path="test"),
                action="agent",
                message_template=(
                    "video_url: https://www.youtube.com/watch?v=3L7wPEB8sEc\n"
                    "video_id: 3L7wPEB8sEc\n"
                    "allow_degraded_transcript_only: true"
                ),
                name="RouteHook",
                session_key="yt_route_3L7wPEB8sEc_allow_degraded",
                to="youtube-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_3L7wPEB8sEc_allow_degraded"
    session = GatewaySession(
        session_id="session_hook_yt_route_3L7wPEB8sEc_allow_degraded",
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
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "10",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS": "0",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway, notification_sink=notifications.append)
        service.config = config
        _bind_workflow_runtime_db(service, tmp_path)
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": False,
                "status": "failed",
                "error": "youtube_transcript_api_failed",
                "failure_class": "api_unavailable",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.2)

    assert service._call_local_youtube_ingest_worker.await_count == 1
    mock_gateway.execute.assert_called()
    gateway_request = mock_gateway.execute.call_args[0][1]
    assert "local_youtube_ingest_status: failed_fail_open" in gateway_request.user_input
    assert gateway_request.metadata["hook_youtube_ingest_status"] == "failed_fail_open"
    assert gateway_request.metadata["hook_youtube_ingest_failure_class"] == "api_unavailable"
    assert gateway_request.metadata["hook_youtube_ingest_fail_open_reason"] == "allow_degraded_transcript_only"
    assert gateway_request.metadata["hook_youtube_ingest_pending_file"] == ""
    assert (workspace_dir / "pending_local_ingest.json").exists() is False
    assert (workspace_dir / "ingestion" / "youtube_local_ingest_result.json").exists()
    assert (workspace_dir / "local_ingest_result.json").exists()
    assert not any(item.get("kind") == "youtube_ingest_failed" for item in notifications)
    progress = next((item for item in notifications if item.get("kind") == "youtube_tutorial_progress"), None)
    assert progress is not None
    assert progress["severity"] == "warning"
    assert progress["metadata"]["ingest_status"] == "failed_fail_open"


@pytest.mark.asyncio
async def test_local_ingest_proxy_failure_emits_proxy_alert_notification(mock_gateway, tmp_path):
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
                session_key="yt_route_dxlyCPGCvy8_proxy_notify",
                to="youtube-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_dxlyCPGCvy8_proxy_notify"
    session = GatewaySession(
        session_id="session_hook_yt_route_dxlyCPGCvy8_proxy_notify",
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
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "1",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS": "0",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway, notification_sink=notifications.append)
        service.config = config
        _bind_workflow_runtime_db(service, tmp_path)
        service._schedule_youtube_retry_attempt = MagicMock()
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": False,
                "status": "failed",
                "error": "youtube_transcript_api_failed",
                "failure_class": "proxy_quota_or_billing",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    assert notifications
    kinds = [item.get("kind") for item in notifications]
    assert "youtube_ingest_failed" in kinds
    assert "youtube_ingest_proxy_alert" in kinds

    proxy_alert = next(item for item in notifications if item.get("kind") == "youtube_ingest_proxy_alert")
    assert proxy_alert["severity"] == "error"
    assert proxy_alert["metadata"]["failure_class"] == "proxy_quota_or_billing"


@pytest.mark.asyncio
async def test_local_ingest_proxy_connect_failure_emits_proxy_alert_notification(mock_gateway, tmp_path):
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
                session_key="yt_route_dxlyCPGCvy8_proxy_connect_notify",
                to="youtube-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_dxlyCPGCvy8_proxy_connect_notify"
    session = GatewaySession(
        session_id="session_hook_yt_route_dxlyCPGCvy8_proxy_connect_notify",
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
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "1",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS": "0",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway, notification_sink=notifications.append)
        service.config = config
        _bind_workflow_runtime_db(service, tmp_path)
        service._schedule_youtube_retry_attempt = MagicMock()
        service._call_local_youtube_ingest_worker = AsyncMock(
            return_value={
                "ok": False,
                "status": "failed",
                "error": "youtube_transcript_api_failed",
                "failure_class": "proxy_connect_failed",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    proxy_alert = next(item for item in notifications if item.get("kind") == "youtube_ingest_proxy_alert")
    assert proxy_alert["severity"] == "error"
    assert proxy_alert["metadata"]["failure_class"] == "proxy_connect_failed"
    assert str(proxy_alert["metadata"]["result_file"]).endswith("local_ingest_result.json")


def test_format_ingest_failure_reason_handles_proxy_pool_unallocated() -> None:
    reason = HooksService._format_ingest_failure_reason(
        error="youtube_transcript_api_failed",
        failure_class="proxy_pool_unallocated",
        attempts=1,
        max_attempts=10,
    )
    assert "no allocated proxies" in reason
    assert "update Infisical secrets" in reason


@pytest.mark.asyncio
async def test_local_ingest_inflight_duplicate_reports_existing_root_cause(mock_gateway, tmp_path):
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
                session_key="yt_route_dxlyCPGCvy8_duplicate_notify",
                to="youtube-expert",
            )
        ],
    )

    workspace_dir = tmp_path / "session_hook_yt_route_dxlyCPGCvy8_duplicate_notify"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    existing_payload = {
        "status": "failed_local_ingest",
        "attempt_count": 3,
        "max_attempts": 10,
        "last_result": {
            "error": "youtube_transcript_api_failed",
            "failure_class": "proxy_connect_failed",
        },
    }
    existing_result_path = workspace_dir / "local_ingest_result.json"
    existing_result_path.write_text(json.dumps(existing_payload), encoding="utf-8")
    original_result_text = existing_result_path.read_text(encoding="utf-8")
    session = GatewaySession(
        session_id="session_hook_yt_route_dxlyCPGCvy8_duplicate_notify",
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
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS": "10",
                "UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS": "0",
                "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN": "0",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway, notification_sink=notifications.append)
        service.config = config
        _bind_workflow_runtime_db(service, tmp_path)
        video_key = service._youtube_ingest_video_key(
            "https://www.youtube.com/watch?v=dxlyCPGCvy8",
            "dxlyCPGCvy8",
        )
        service._youtube_ingest_inflight[video_key] = time.time() + 60
        service._youtube_ingest_inflight_owners[video_key] = {
            "session_id": session.session_id,
            "workspace_root": str(workspace_dir),
        }
        service._call_local_youtube_ingest_worker = AsyncMock()

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer secret-token"}
        request.body = AsyncMock(return_value=b"{}")
        request.query_params = {}

        response = await service.handle_request(request, "test")
        assert response.status_code == 200
        await asyncio.sleep(0.1)

    service._call_local_youtube_ingest_worker.assert_not_awaited()
    duplicate_notice = next(item for item in notifications if item.get("kind") == "youtube_ingest_failed")
    assert duplicate_notice["title"] == "YouTube Ingest Duplicate Suppressed"
    assert duplicate_notice["severity"] == "warning"
    assert duplicate_notice["requires_action"] is False
    assert duplicate_notice["metadata"]["failure_class"] == "inflight_duplicate"
    assert duplicate_notice["metadata"]["root_failure_class"] == "proxy_connect_failed"
    assert duplicate_notice["metadata"]["owner_session_id"] == session.session_id
    assert duplicate_notice["metadata"]["root_result_file"] == str(existing_result_path)
    assert "Existing root cause" in duplicate_notice["message"]
    assert "Residential proxy CONNECT failed" in duplicate_notice["message"]
    assert existing_result_path.read_text(encoding="utf-8") == original_result_text
    assert not any(item.get("kind") == "youtube_ingest_proxy_alert" for item in notifications)


def test_vps_local_worker_prefers_loopback_only(mock_gateway):
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_DEPLOYMENT_PROFILE": "vps",
                "UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker",
                "UA_HOOKS_YOUTUBE_INGEST_URLS": "http://100.95.187.38:8002/api/v1/youtube/ingest,http://127.0.0.1:8002/api/v1/youtube/ingest",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
    assert service._youtube_ingest_urls == ["http://127.0.0.1:8002/api/v1/youtube/ingest"]


def test_local_workstation_local_worker_reorders_loopback_first(mock_gateway):
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {
                "UA_DEPLOYMENT_PROFILE": "local_workstation",
                "UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker",
                "UA_HOOKS_YOUTUBE_INGEST_URLS": "http://100.95.187.38:8002/api/v1/youtube/ingest,http://127.0.0.1:8002/api/v1/youtube/ingest",
            },
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
    assert service._youtube_ingest_urls == [
        "http://127.0.0.1:8002/api/v1/youtube/ingest",
        "http://100.95.187.38:8002/api/v1/youtube/ingest",
    ]


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
        third = await service.handle_request(request, "test")
        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 200

        await asyncio.sleep(0.2)

    overflow_notifications = [item for item in notifications if item.get("kind") == "hook_dispatch_queue_overflow"]
    assert len(overflow_notifications) == 1
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
        _bind_workflow_runtime_db(service, tmp_path)

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
    assert action.to == "youtube-expert"
    assert "km5fvKPRsJw" in (action.message or "")
    marker = session_dir / ".hook_startup_recovery.json"
    assert marker.exists()


@pytest.mark.asyncio
async def test_recover_interrupted_youtube_sessions_backfills_pending_local_ingest(mock_gateway, tmp_path):
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
        _bind_workflow_runtime_db(service, tmp_path)

    session_id = "session_hook_yt_UCYHosdETLPp6dpJEsgIUTmw_3L7wPEB8sEc"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    pending_path = session_dir / "pending_local_ingest.json"
    pending_path.write_text(
        json.dumps(
            {
                "status": "failed_local_ingest",
                "session_id": session_id,
                "video_url": "https://www.youtube.com/watch?v=3L7wPEB8sEc",
                "video_id": "3L7wPEB8sEc",
                "created_at_epoch": 1.0,
            }
        ),
        encoding="utf-8",
    )

    service._dispatch_action = AsyncMock(return_value=None)
    recovered = await service.recover_interrupted_youtube_sessions(tmp_path)
    assert recovered == 1
    await asyncio.sleep(0.05)
    service._dispatch_action.assert_called_once()
    action = service._dispatch_action.call_args.args[0]
    assert action.to == "youtube-expert"
    assert action.name == "RecoveredPendingLocalIngest"
    assert "allow_degraded_transcript_only: true" in (action.message or "")
    marker = session_dir / ".hook_startup_recovery.json"
    assert marker.exists()


@pytest.mark.asyncio
async def test_recover_interrupted_youtube_sessions_supports_run_workspace_prefix(mock_gateway, tmp_path):
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
        _bind_workflow_runtime_db(service, tmp_path)

    session_id = "session_hook_yt_UCYHosdETLPp6dpJEsgIUTmw_runprefix123"
    session_dir = tmp_path / f"run_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    pending_path = session_dir / "pending_local_ingest.json"
    pending_path.write_text(
        json.dumps(
            {
                "status": "failed_local_ingest",
                "session_id": session_id,
                "video_url": "https://www.youtube.com/watch?v=runprefix123",
                "video_id": "runprefix123",
                "created_at_epoch": 1.0,
            }
        ),
        encoding="utf-8",
    )

    service._dispatch_action = AsyncMock(return_value=None)
    recovered = await service.recover_interrupted_youtube_sessions(tmp_path)
    assert recovered == 1
    await asyncio.sleep(0.05)
    service._dispatch_action.assert_called_once()
    action = service._dispatch_action.call_args.args[0]
    assert action.to == "youtube-expert"
    assert action.name == "RecoveredPendingLocalIngest"
    assert "runprefix123" in (action.message or "")
    marker = session_dir / ".hook_startup_recovery.json"
    assert marker.exists()


@pytest.mark.asyncio
async def test_recover_interrupted_youtube_sessions_skips_non_retryable_pending_local_ingest(
    mock_gateway, tmp_path
):
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
        _bind_workflow_runtime_db(service, tmp_path)

    session_id = "session_hook_yt_UCYHosdETLPp6dpJEsgIUTmw_3L7wPEB8sEc"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    pending_path = session_dir / "pending_local_ingest.json"
    pending_path.write_text(
        json.dumps(
            {
                "status": "failed_local_ingest",
                "session_id": session_id,
                "video_url": "https://www.youtube.com/watch?v=3L7wPEB8sEc",
                "video_id": "3L7wPEB8sEc",
                "last_result": {"failure_class": "proxy_not_configured"},
                "created_at_epoch": 1.0,
            }
        ),
        encoding="utf-8",
    )

    service._dispatch_action = AsyncMock(return_value=None)
    recovered = await service.recover_interrupted_youtube_sessions(tmp_path)
    assert recovered == 0
    await asyncio.sleep(0.05)
    service._dispatch_action.assert_not_called()
    marker = session_dir / ".hook_startup_recovery.json"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_recover_interrupted_youtube_sessions_backfills_pending_dispatch_interrupt(mock_gateway, tmp_path):
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
        _bind_workflow_runtime_db(service, tmp_path)

    session_id = "session_hook_yt_UCYHosdETLPp6dpJEsgIUTmw_3L7wPEB8sEc"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    pending_path = session_dir / "pending_hook_recovery.json"
    pending_path.write_text(
        json.dumps(
            {
                "status": "dispatch_interrupted",
                "session_id": session_id,
                "video_id": "3L7wPEB8sEc",
                "reason": "hook_dispatch_interrupted",
                "created_at_epoch": 1.0,
            }
        ),
        encoding="utf-8",
    )

    service._dispatch_action = AsyncMock(return_value=None)
    recovered = await service.recover_interrupted_youtube_sessions(tmp_path)
    assert recovered == 1
    await asyncio.sleep(0.05)
    service._dispatch_action.assert_called_once()
    action = service._dispatch_action.call_args.args[0]
    assert action.to == "youtube-expert"
    assert "3L7wPEB8sEc" in (action.message or "")
    marker = session_dir / ".hook_startup_recovery.json"
    assert marker.exists()


def test_build_manual_youtube_action_builds_direct_agent_payload():
    action = build_manual_youtube_action(
        {
            "video_url": "https://www.youtube.com/watch?v=demo1234567",
            "channel_id": "UCdemo-channel",
            "title": "Python MCP automation walkthrough",
            "mode": "auto",
            "allow_degraded_transcript_only": True,
        },
        name="WatcherTestHook",
    )

    assert action is not None
    assert action["name"] == "WatcherTestHook"
    assert action["to"] == "youtube-expert"
    assert action["session_key"] == "yt_UCdemo-channel_demo1234567"
    assert "learning_mode: concept_plus_implementation" in action["message"]
    assert "allow_degraded_transcript_only: true" in action["message"]


def test_validate_youtube_tutorial_artifacts_allows_concept_only_without_implementation(
    mock_gateway,
    tmp_path,
):
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)

    run_dir = (
        tmp_path
        / "youtube-tutorial-creation"
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
        / "youtube-tutorial-creation"
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


def test_validate_youtube_tutorial_artifacts_generates_repo_scripts_for_implementation(
    mock_gateway,
    tmp_path,
):
    with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
        service = HooksService(mock_gateway)

    run_dir = (
        tmp_path
        / "youtube-tutorial-creation"
        / "2026-02-25"
        / "code-video__030303"
    )
    implementation_dir = run_dir / "implementation"
    implementation_dir.mkdir(parents=True, exist_ok=True)
    (implementation_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (run_dir / "README.md").write_text("# Readme\n", encoding="utf-8")
    (run_dir / "CONCEPT.md").write_text("# Concept\n", encoding="utf-8")
    (run_dir / "IMPLEMENTATION.md").write_text("# Impl\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": "code789",
                "title": "Code Mode Scripts",
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
        result = service._validate_youtube_tutorial_artifacts(
            video_id="code789",
            started_at_epoch=time.time(),
        )

    create_script = implementation_dir / "create_new_repo.sh"
    delete_script = implementation_dir / "deletethisrepo.sh"
    assert create_script.exists()
    assert delete_script.exists()
    assert create_script.stat().st_mode & 0o111
    assert delete_script.stat().st_mode & 0o111

    create_content = create_script.read_text(encoding="utf-8")
    assert "uv init" in create_content
    assert "uv add -r requirements.txt" in create_content
    assert "uv sync" in create_content
    assert "uv run app.py" in create_content
    assert service._tutorial_bootstrap_repo_root in create_content
    assert "deletethisrepo.sh" in create_content

    delete_content = delete_script.read_text(encoding="utf-8")
    assert "rm -rf \"$THIS_DIR\"" in delete_content

    bootstrap_scripts = result.get("bootstrap_scripts") or []
    assert str(create_script) in bootstrap_scripts
    assert str(delete_script) in bootstrap_scripts
    key_file_names = {entry.get("name") for entry in result.get("key_files", [])}
    assert "create_new_repo.sh" not in key_file_names
    assert "deletethisrepo.sh" not in key_file_names
    assert "main.py" in key_file_names
