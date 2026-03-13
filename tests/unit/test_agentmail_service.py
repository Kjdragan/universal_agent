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
def _env(monkeypatch, tmp_path):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))


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


@pytest.fixture
async def service_with_queue(mock_agentmail_client, monkeypatch, tmp_path):
    from universal_agent.services.agentmail_service import AgentMailService

    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))
    dispatch_fn = AsyncMock(return_value=(True, "agent"))
    dispatch_with_admission_fn = AsyncMock(return_value={"decision": "accepted", "status": "completed"})

    svc = AgentMailService(
        dispatch_fn=dispatch_fn,
        dispatch_with_admission_fn=dispatch_with_admission_fn,
        notification_sink=MagicMock(),
    )
    svc._client = mock_agentmail_client
    await svc._ensure_inbox()
    svc._enabled = True
    svc._started = True
    svc._ensure_queue_schema()
    svc._queue_task = asyncio.create_task(svc._trusted_inbox_queue_loop())
    svc._queue_wakeup.set()

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
        assert status["trusted_sender_count"] == 3
        assert "kevin@clearspringcg.com" in status["trusted_senders"]


class TestNotifications:
    @pytest.mark.asyncio
    async def test_draft_emits_notification(self, service):
        await service.send_email(to="x@y.com", subject="s", text="t")
        service._notification_sink.assert_called()
        call_args = service._notification_sink.call_args[0][0]
        assert call_args["kind"] == "agentmail_draft_created"


class TestReplyExtraction:
    """Tests for _extract_reply_text — strips quoted thread history from replies."""

    def test_extracts_gmail_reply(self):
        from universal_agent.services.agentmail_service import _extract_reply_text

        body = (
            "Thanks, I'll look into the proxy issue on channel X.\n"
            "\n"
            "On Thu, Mar 6, 2026 at 10:30 AM, Simone D <oddcity216@agentmail.to> wrote:\n"
            "> YouTube RSS Digest for March 6, 2026\n"
            "> \n"
            "> Channel: TechCrunch - 3 new videos\n"
        )
        result = _extract_reply_text(body)
        assert "proxy issue" in result
        assert "YouTube RSS Digest" not in result
        assert "oddcity216@agentmail.to" not in result


class TestTrustedInboundHandling:
    @pytest.mark.asyncio
    async def test_trusted_inbound_sends_ack_and_dispatches_metadata(
        self, service, mock_agentmail_client
    ):
        class _Message:
            from_ = "Kevin Dragan <kevin.dragan@outlook.com>"
            subject = "Hello Simone"
            thread_id = "thd_direct_001"
            message_id = "msg_direct_001"
            text = "hello there"
            html = "<p>hello there</p>"
            attachments = []

        class _Event:
            message = _Message()

        await service._handle_inbound_email(_Event())

        mock_agentmail_client.inboxes.messages.reply.assert_awaited_once()
        dispatch_payload = service._dispatch_fn.await_args.args[0]
        assert dispatch_payload["to"] == "email-handler"
        assert "sender_email: kevin.dragan@outlook.com" in dispatch_payload["message"]
        assert "sender_role: trusted_operator" in dispatch_payload["message"]
        assert "sender_trusted: True" in dispatch_payload["message"]

    @pytest.mark.asyncio
    async def test_untrusted_inbound_skips_ack(self, service, mock_agentmail_client):
        class _Message:
            from_ = "Random Person <random@example.com>"
            subject = "Hi"
            thread_id = "thd_random_001"
            message_id = "msg_random_001"
            text = "hello"
            html = "<p>hello</p>"
            attachments = []

        class _Event:
            message = _Message()

        await service._handle_inbound_email(_Event())

        mock_agentmail_client.inboxes.messages.reply.assert_not_awaited()
        dispatch_payload = service._dispatch_fn.await_args.args[0]
        assert "sender_email: random@example.com" in dispatch_payload["message"]
        assert "sender_role: external" in dispatch_payload["message"]
        assert "sender_trusted: False" in dispatch_payload["message"]

    @pytest.mark.asyncio
    async def test_trusted_inbound_queue_completes_when_service_with_queue_is_free(
        self, service_with_queue, mock_agentmail_client
    ):
        class _Message:
            from_ = "Kevin Dragan <kevin@clearspringcg.com>"
            subject = "Please investigate"
            thread_id = "thd_queue_001"
            message_id = "msg_queue_001"
            text = "please investigate this directly"
            html = "<p>please investigate this directly</p>"
            attachments = []

        class _Event:
            message = _Message()

        await service_with_queue._handle_inbound_email(_Event())
        await asyncio.sleep(0.2)

        items = service_with_queue.list_inbox_queue(limit=10, trusted_only=True)
        assert len(items) == 1
        assert items[0]["status"] == "completed"
        assert items[0]["ack_status"] == "sent"
        assert items[0]["sender_role"] == "trusted_operator"
        service_with_queue._dispatch_with_admission_fn.assert_awaited_once()
        mock_agentmail_client.inboxes.messages.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trusted_inbound_queue_retries_when_busy(
        self, service_with_queue, mock_agentmail_client, monkeypatch
    ):
        service_with_queue._dispatch_with_admission_fn = AsyncMock(
            side_effect=[
                {"decision": "busy", "reason": "busy"},
                {"decision": "accepted", "status": "completed"},
            ]
        )
        monkeypatch.setattr(
            service_with_queue,
            "_trusted_queue_retry_base_seconds",
            0.01,
            raising=False,
        )
        monkeypatch.setattr(
            service_with_queue,
            "_trusted_queue_retry_max_seconds",
            0.01,
            raising=False,
        )
        monkeypatch.setattr(
            service_with_queue,
            "_trusted_queue_retry_jitter_ratio",
            0.0,
            raising=False,
        )
        monkeypatch.setattr(
            service_with_queue,
            "_trusted_queue_poll_seconds",
            0.01,
            raising=False,
        )

        class _Message:
            from_ = "Kevin Dragan <kevin.dragan@outlook.com>"
            subject = "Retry me"
            thread_id = "thd_queue_002"
            message_id = "msg_queue_002"
            text = "hello"
            html = "<p>hello</p>"
            attachments = []

        class _Event:
            message = _Message()

        await service_with_queue._handle_inbound_email(_Event())
        await asyncio.sleep(0.25)

        item = service_with_queue.list_inbox_queue(limit=10, trusted_only=True)[0]
        assert item["status"] == "completed"
        assert item["attempt_count"] == 2
        assert service_with_queue._dispatch_with_admission_fn.await_count == 2
        mock_agentmail_client.inboxes.messages.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trusted_inbound_calls_trusted_ingress_hook_immediately(
        self, mock_agentmail_client
    ):
        from universal_agent.services.agentmail_service import AgentMailService

        trusted_ingress_fn = MagicMock(return_value={"triggered": True, "count": 1})
        svc = AgentMailService(
            dispatch_fn=AsyncMock(return_value=(True, "agent")),
            dispatch_with_admission_fn=AsyncMock(return_value={"decision": "accepted"}),
            notification_sink=MagicMock(),
            trusted_ingress_fn=trusted_ingress_fn,
        )
        svc._client = mock_agentmail_client
        await svc._ensure_inbox()
        svc._enabled = True
        svc._started = True

        class _Message:
            from_ = "Kevin Dragan <kevin.dragan@outlook.com>"
            subject = "Heartbeat"
            thread_id = "thd_ingress_001"
            message_id = "msg_ingress_001"
            text = "Run another heartbeat and check if it is fixed"
            html = "<p>Run another heartbeat and check if it is fixed</p>"
            attachments = []

        class _Event:
            message = _Message()

        try:
            await svc._handle_inbound_email(_Event())
        finally:
            await svc.shutdown()

        trusted_ingress_fn.assert_called_once()
        payload = trusted_ingress_fn.call_args.args[0]
        assert payload["name"] == "AgentMailInbound"
        assert "Run another heartbeat and check if it is fixed" in payload["message"]


class TestTrustedSenderHelpers:
    def test_normalizes_sender_email_from_display_name(self):
        from universal_agent.services.agentmail_service import _normalize_sender_email

        assert (
            _normalize_sender_email("Kevin Dragan <kevin@clearspringcg.com>")
            == "kevin@clearspringcg.com"
        )

    def test_extracts_outlook_reply(self):
        from universal_agent.services.agentmail_service import _extract_reply_text

        body = (
            "Investigate the Fireship transcript failure please.\n"
            "\n"
            "________________________________________\n"
            "From: Simone D [oddcity216@agentmail.to]\n"
            "Sent: Thursday, March 6, 2026 10:30 AM\n"
            "To: Kevin Dragan\n"
            "Subject: YouTube RSS Digest\n"
            "\n"
            "Here is your daily digest...\n"
        )
        result = _extract_reply_text(body)
        assert "Investigate" in result
        assert "daily digest" not in result

    def test_preserves_body_when_no_quotes(self):
        from universal_agent.services.agentmail_service import _extract_reply_text

        body = "Please check the proxy configuration."
        result = _extract_reply_text(body)
        assert result == body

    def test_returns_full_body_on_empty_extraction(self):
        from universal_agent.services.agentmail_service import _extract_reply_text

        # If the entire email is quoted (e.g. forwarded), return full body
        body = "> This is all quoted\n> No new content"
        result = _extract_reply_text(body)
        # Should not be empty — falls back to full body
        assert len(result) > 0

    def test_handles_empty_body(self):
        from universal_agent.services.agentmail_service import _extract_reply_text

        assert _extract_reply_text("") == ""
        assert _extract_reply_text("   ") == "   "

    def test_strips_quote_markers(self):
        from universal_agent.services.agentmail_service import _extract_reply_text

        body = (
            "Got it, will do.\n"
            "\n"
            "> On Mar 6, 2026, Simone wrote:\n"
            "> Here is your digest\n"
            "> Channel updates...\n"
        )
        result = _extract_reply_text(body)
        assert "Got it" in result
        assert "Channel updates" not in result


class TestInboundReplyExtraction:
    """Integration test: _handle_inbound_email uses reply extraction."""

    @pytest.mark.asyncio
    async def test_inbound_dispatch_contains_extracted_reply(self, service):
        # Build a fake inbound event with quoted thread
        mock_event = MagicMock()
        mock_event.message.from_ = "kevinjdragan@gmail.com"
        mock_event.message.subject = "Re: YouTube RSS Digest"
        mock_event.message.thread_id = "thd_123"
        mock_event.message.message_id = "msg_456"
        mock_event.message.text = (
            "Check the Fireship failure.\n"
            "\n"
            "On Thu, Mar 6, 2026 at 10:30 AM, Simone D <oddcity216@agentmail.to> wrote:\n"
            "> YouTube RSS Digest for March 6, 2026\n"
            "> Failures: 2 transcripts failed\n"
        )
        mock_event.message.html = ""

        await service._handle_inbound_email(mock_event)

        # Verify dispatch was called
        service._dispatch_fn.assert_awaited_once()
        payload = service._dispatch_fn.call_args[0][0]

        # The message should contain the clean reply prominently
        assert "--- Reply (new content) ---" in payload["message"]
        assert "Check the Fireship failure" in payload["message"]
        # Full body should be included as reference since extraction happened
        assert "--- Full Email Body (for reference) ---" in payload["message"]
        assert "reply_extracted: True" in payload["message"]

    @pytest.mark.asyncio
    async def test_inbound_no_quotes_skips_full_body_section(self, service):
        mock_event = MagicMock()
        mock_event.message.from_ = "kevinjdragan@gmail.com"
        mock_event.message.subject = "Quick question"
        mock_event.message.thread_id = "thd_789"
        mock_event.message.message_id = "msg_012"
        mock_event.message.text = "What is the status of the proxy?"
        mock_event.message.html = ""

        await service._handle_inbound_email(mock_event)

        payload = service._dispatch_fn.call_args[0][0]
        # No quotes → no extraction → no "Full Email Body" section
        assert "--- Full Email Body (for reference) ---" not in payload["message"]
        assert "reply_extracted: False" in payload["message"]
        assert "status of the proxy" in payload["message"]


class TestInboxPolling:
    @pytest.mark.asyncio
    async def test_poll_loop_processes_new_inbound_messages_once(self, service, mock_agentmail_client):
        inbound = MagicMock()
        inbound.from_ = "Kevin Dragan <kevinjdragan@gmail.com>"
        inbound.subject = "Re: heartbeat"
        inbound.thread_id = "thd_poll_001"
        inbound.message_id = "msg_poll_001"
        inbound.text = "Run another heartbeat now."
        inbound.html = ""
        inbound.attachments = []

        outbound = MagicMock()
        outbound.from_ = "Simone D <simone@testdomain.com>"
        outbound.subject = "Sent by Simone"
        outbound.thread_id = "thd_poll_002"
        outbound.message_id = "msg_poll_002"
        outbound.text = "Operator summary"
        outbound.html = ""
        outbound.attachments = []

        listing = MagicMock()
        listing.messages = [outbound, inbound]
        mock_agentmail_client.inboxes.messages.list = AsyncMock(return_value=listing)
        mock_agentmail_client.inboxes.messages.get = AsyncMock(return_value=inbound)

        await service._poll_inbox_once(limit=10)
        await service._poll_inbox_once(limit=10)

        # Inbound processed once; outbound ignored as self-authored.
        assert service._dispatch_fn.await_count == 1
        dispatch_payload = service._dispatch_fn.await_args.args[0]
        assert dispatch_payload["session_key"] == "agentmail_thd_poll_001"
        mock_agentmail_client.inboxes.messages.get.assert_awaited_once_with(
            inbox_id="simone@testdomain.com",
            message_id="msg_poll_001",
        )

    @pytest.mark.asyncio
    async def test_poll_loop_hydrates_full_message_before_dispatch(self, service, mock_agentmail_client):
        preview = MagicMock()
        preview.from_ = "Kevin Dragan <kevinjdragan@gmail.com>"
        preview.subject = "Heartbeat"
        preview.thread_id = "thd_poll_010"
        preview.message_id = "msg_poll_010"
        preview.text = ""
        preview.html = ""
        preview.attachments = []

        full = MagicMock()
        full.from_ = preview.from_
        full.subject = preview.subject
        full.thread_id = preview.thread_id
        full.message_id = preview.message_id
        full.text = "Run another heartbeat and check if it is fixed"
        full.html = "<p>Run another heartbeat and check if it is fixed</p>"
        full.attachments = []

        listing = MagicMock()
        listing.messages = [preview]
        mock_agentmail_client.inboxes.messages.list = AsyncMock(return_value=listing)
        mock_agentmail_client.inboxes.messages.get = AsyncMock(return_value=full)

        await service._poll_inbox_once(limit=10)

        dispatch_payload = service._dispatch_fn.await_args.args[0]
        assert "Run another heartbeat and check if it is fixed" in dispatch_payload["message"]
        mock_agentmail_client.inboxes.messages.get.assert_awaited_once_with(
            inbox_id="simone@testdomain.com",
            message_id="msg_poll_010",
        )

    @pytest.mark.asyncio
    async def test_poll_loop_skips_already_seen_message_id(self, service, mock_agentmail_client):
        inbound = MagicMock()
        inbound.from_ = "Kevin Dragan <kevinjdragan@gmail.com>"
        inbound.subject = "Re: status"
        inbound.thread_id = "thd_poll_003"
        inbound.message_id = "msg_poll_003"
        inbound.text = "Any update?"
        inbound.html = ""
        inbound.attachments = []

        listing = MagicMock()
        listing.messages = [inbound]
        mock_agentmail_client.inboxes.messages.list = AsyncMock(return_value=listing)

        assert service._claim_seen_message_id("msg_poll_003") is True
        await service._poll_inbox_once(limit=10)

        service._dispatch_fn.assert_not_awaited()


class TestWebSocketFailOpen:
    @pytest.mark.asyncio
    async def test_ws_fail_open_stops_reconnect_loop_for_rate_limit(self, monkeypatch):
        from universal_agent.services.agentmail_service import AgentMailService

        svc = AgentMailService()
        svc._enabled = True
        svc._client = MagicMock()
        svc._inbox_id = "simone@testdomain.com"
        svc._ws_fail_open_after_attempts = 1
        monkeypatch.setattr("universal_agent.services.agentmail_service.random.uniform", lambda *_: 0.0)

        async def _raise_rate_limited():
            raise RuntimeError("WebSocketClosedError(status_code: 429)")

        monkeypatch.setattr(svc, "_ws_connect_and_listen", _raise_rate_limited)

        await svc._ws_loop()

        assert svc._ws_fail_opened is True
        assert svc._ws_last_status_code == 429
        assert svc._last_error == "ws_fail_open_status_429"

    @pytest.mark.asyncio
    async def test_ws_non_fail_open_status_keeps_retrying_until_stop(self, monkeypatch):
        from universal_agent.services.agentmail_service import AgentMailService

        svc = AgentMailService()
        svc._enabled = True
        svc._client = MagicMock()
        svc._inbox_id = "simone@testdomain.com"
        svc._ws_fail_open_after_attempts = 1
        monkeypatch.setattr("universal_agent.services.agentmail_service.random.uniform", lambda *_: 0.0)

        async def _raise_server_error():
            raise RuntimeError("WebSocketClosedError(status_code: 500)")

        monkeypatch.setattr(svc, "_ws_connect_and_listen", _raise_server_error)
        loop_task = asyncio.create_task(svc._ws_loop())
        await asyncio.sleep(0.01)
        svc._ws_stop_event.set()
        await asyncio.wait_for(loop_task, timeout=2)

        assert svc._ws_fail_opened is False
        assert svc._ws_last_status_code == 500
        assert svc._ws_reconnect_count >= 1


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
