"""Unit tests for AgentMailService."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch env before importing the service
_BASE_ENV = {
    "UA_AGENTMAIL_ENABLED": "1",
    "AGENTMAIL_API_KEY": "test-api-key-123",
    "UA_AGENTMAIL_INBOX_ADDRESS": "simone@testdomain.com",
    "UA_AGENTMAIL_AUTO_SEND": "0",
    "UA_AGENTMAIL_WS_ENABLED": "0",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def mock_agentmail_client():
    """Create a mock AsyncAgentMail client with nested resource mocking."""
    client = MagicMock()

    # Mock inbox
    mock_inbox = MagicMock()
    mock_inbox.inbox_id = "simone@testdomain.com"
    client.inboxes.get = AsyncMock(return_value=mock_inbox)
    client.inboxes.create = AsyncMock(return_value=mock_inbox)

    # Mock messages
    mock_msg = MagicMock()
    mock_msg.message_id = "msg_test_001"
    mock_msg.thread_id = "thd_test_001"
    mock_msg.from_ = "sender@example.com"
    mock_msg.to = "simone@testdomain.com"
    mock_msg.subject = "Test Subject"
    mock_msg.text = "Test body"
    mock_msg.html = "<p>Test body</p>"
    mock_msg.labels = ["received"]
    mock_msg.attachments = []
    mock_msg.created_at = "2026-03-03T00:00:00Z"

    mock_messages_list = MagicMock()
    mock_messages_list.messages = [mock_msg]

    client.inboxes.messages.send = AsyncMock(return_value=mock_msg)
    client.inboxes.messages.reply = AsyncMock(return_value=mock_msg)
    client.inboxes.messages.list = AsyncMock(return_value=mock_messages_list)
    client.inboxes.messages.get = AsyncMock(return_value=mock_msg)

    # Mock drafts
    mock_draft = MagicMock()
    mock_draft.draft_id = "drf_test_001"
    client.inboxes.drafts.create = AsyncMock(return_value=mock_draft)
    client.inboxes.drafts.send = AsyncMock(return_value=mock_msg)

    # Mock threads
    mock_thread = MagicMock()
    mock_thread.thread_id = "thd_test_001"
    mock_thread.subject = "Test Thread"
    mock_thread.labels = []
    mock_thread.message_count = 2
    mock_thread.created_at = "2026-03-03T00:00:00Z"

    mock_threads_list = MagicMock()
    mock_threads_list.threads = [mock_thread]
    client.inboxes.threads.list = AsyncMock(return_value=mock_threads_list)

    return client


@pytest.fixture
async def service(mock_agentmail_client):
    """Create and start an AgentMailService with mocked client."""
    from universal_agent.services.agentmail_service import AgentMailService

    svc = AgentMailService(
        dispatch_fn=AsyncMock(return_value=(True, "agent")),
        notification_sink=MagicMock(),
    )

    # Inject mock client directly (bypassing lazy import in startup)
    svc._client = mock_agentmail_client
    await svc._ensure_inbox()
    svc._enabled = True
    svc._started = True

    yield svc
    await svc.shutdown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestServiceInit:
    def test_disabled_when_env_off(self, monkeypatch):
        monkeypatch.setenv("UA_AGENTMAIL_ENABLED", "0")
        from universal_agent.services.agentmail_service import AgentMailService

        svc = AgentMailService()
        assert svc._enabled is False

    def test_enabled_when_env_on(self, monkeypatch):
        monkeypatch.setenv("UA_AGENTMAIL_ENABLED", "1")
        from universal_agent.services.agentmail_service import AgentMailService

        svc = AgentMailService()
        assert svc._enabled is True


class TestInboxResolution:
    @pytest.mark.asyncio
    async def test_resolves_configured_address(self, service):
        assert service.get_inbox_address() == "simone@testdomain.com"

    @pytest.mark.asyncio
    async def test_inbox_id_set(self, service):
        assert service._inbox_id == "simone@testdomain.com"


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_creates_draft_by_default(self, service, mock_agentmail_client):
        result = await service.send_email(
            to="kevin@example.com",
            subject="Test Report",
            text="Here is your report.",
        )
        assert result["status"] == "draft"
        assert "draft_id" in result
        mock_agentmail_client.inboxes.drafts.create.assert_awaited_once()
        mock_agentmail_client.inboxes.messages.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_directly_with_force(self, service, mock_agentmail_client):
        result = await service.send_email(
            to="kevin@example.com",
            subject="Urgent",
            text="Important message",
            force_send=True,
        )
        assert result["status"] == "sent"
        assert "message_id" in result
        mock_agentmail_client.inboxes.messages.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sends_directly_with_auto_send(self, service, mock_agentmail_client, monkeypatch):
        monkeypatch.setenv("UA_AGENTMAIL_AUTO_SEND", "1")
        result = await service.send_email(
            to="kevin@example.com",
            subject="Auto",
            text="Auto-sent",
        )
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_send_increments_counter(self, service):
        assert service._messages_sent == 0
        await service.send_email(
            to="x@y.com", subject="s", text="t", force_send=True,
        )
        assert service._messages_sent == 1

    @pytest.mark.asyncio
    async def test_draft_increments_counter(self, service):
        assert service._drafts_created == 0
        await service.send_email(to="x@y.com", subject="s", text="t")
        assert service._drafts_created == 1


class TestSendDraft:
    @pytest.mark.asyncio
    async def test_send_draft_approves(self, service, mock_agentmail_client):
        result = await service.send_draft("drf_test_001")
        assert result["status"] == "sent"
        mock_agentmail_client.inboxes.drafts.send.assert_awaited_once()


class TestReply:
    @pytest.mark.asyncio
    async def test_reply_sends(self, service, mock_agentmail_client):
        result = await service.reply(
            message_id="msg_test_001",
            text="Thanks!",
        )
        assert result["status"] == "sent"
        mock_agentmail_client.inboxes.messages.reply.assert_awaited_once()


class TestListMessages:
    @pytest.mark.asyncio
    async def test_list_returns_messages(self, service):
        messages = await service.list_messages()
        assert len(messages) == 1
        assert messages[0]["subject"] == "Test Subject"

    @pytest.mark.asyncio
    async def test_get_message(self, service):
        msg = await service.get_message("msg_test_001")
        assert msg["message_id"] == "msg_test_001"
        assert msg["subject"] == "Test Subject"


class TestListThreads:
    @pytest.mark.asyncio
    async def test_list_threads(self, service):
        threads = await service.list_threads()
        assert len(threads) == 1
        assert threads[0]["thread_id"] == "thd_test_001"


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_fields(self, service):
        status = service.status()
        assert status["enabled"] is True
        assert status["inbox_address"] == "simone@testdomain.com"
        assert status["auto_send"] is False
        assert status["ws_connected"] is False
        assert isinstance(status["messages_sent"], int)
        assert isinstance(status["drafts_created"], int)


class TestNotifications:
    @pytest.mark.asyncio
    async def test_draft_emits_notification(self, service):
        await service.send_email(to="x@y.com", subject="s", text="t")
        service._notification_sink.assert_called()
        call_args = service._notification_sink.call_args[0][0]
        assert call_args["kind"] == "agentmail_draft_created"


class TestAssertReady:
    @pytest.mark.asyncio
    async def test_raises_when_disabled(self):
        from universal_agent.services.agentmail_service import AgentMailService

        svc = AgentMailService()
        svc._enabled = False
        with pytest.raises(RuntimeError, match="not enabled"):
            await svc.send_email(to="x@y.com", subject="s", text="t")

    @pytest.mark.asyncio
    async def test_raises_when_no_inbox(self, monkeypatch):
        monkeypatch.setenv("UA_AGENTMAIL_ENABLED", "1")
        from universal_agent.services.agentmail_service import AgentMailService

        svc = AgentMailService()
        svc._client = MagicMock()
        svc._inbox_id = ""
        with pytest.raises(RuntimeError, match="inbox not configured"):
            await svc.send_email(to="x@y.com", subject="s", text="t")
