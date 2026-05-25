"""Tests for the cron_artifact_reminders_sweep script's mail-service shim.

PR #446 wired the sweep as a !script subprocess that called
``getattr(gateway_server, '_agentmail_service', None)`` — which always
returned None in a subprocess because the gateway's lifespan startup
never runs there (separate module copy). This test guards the
replacement shim: a self-contained ``AsyncAgentMail`` wrapper that
matches the parent gateway's ``send_email`` interface.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from universal_agent.scripts.cron_artifact_reminders_sweep import (
    _SubprocessMailService,
)


@pytest.mark.asyncio
async def test_send_email_returns_skipped_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    svc = _SubprocessMailService()
    result = await svc.send_email(
        to="kevinjdragan@gmail.com",
        subject="test",
        text="hi",
    )
    assert result["status"] == "skipped"
    assert result["message_id"] == ""


@pytest.mark.asyncio
async def test_send_email_succeeds_when_sdk_returns_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub the ``AsyncAgentMail`` SDK to confirm the shim parses its
    response into the expected ``{status, message_id, thread_id}``
    shape — matching what the cron_artifact_reminders sweep expects."""
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test-key-123")
    monkeypatch.setenv("UA_AGENTMAIL_INBOX_ADDRESS", "test@agentmail.to")

    fake_inbox = MagicMock()
    fake_inbox.inbox_id = "inbox_abc"
    fake_inbox.address = "test@agentmail.to"

    fake_msg = MagicMock()
    fake_msg.message_id = "msg_xyz"
    fake_msg.thread_id = "thread_xyz"

    fake_client = MagicMock()
    fake_client.inboxes.get = AsyncMock(return_value=fake_inbox)
    fake_client.inboxes.messages.send = AsyncMock(return_value=fake_msg)

    # Stub the SDK import so we don't need the real package.
    import sys
    import types
    fake_module = types.ModuleType("agentmail")
    fake_module.AsyncAgentMail = MagicMock(return_value=fake_client)
    monkeypatch.setitem(sys.modules, "agentmail", fake_module)

    svc = _SubprocessMailService()
    result = await svc.send_email(
        to="kevinjdragan@gmail.com",
        subject="Test artifact reminder",
        text="reminder text",
        html="<p>reminder html</p>",
    )

    assert result["status"] == "sent"
    assert result["message_id"] == "msg_xyz"
    assert result["thread_id"] == "thread_xyz"
    # Confirm the SDK was called with the resolved inbox + recipient
    fake_client.inboxes.messages.send.assert_called_once()
    call_kwargs = fake_client.inboxes.messages.send.call_args.kwargs
    assert call_kwargs["inbox_id"] == "inbox_abc"
    assert call_kwargs["to"] == "kevinjdragan@gmail.com"


@pytest.mark.asyncio
async def test_send_email_returns_failed_on_sdk_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test-key-123")
    monkeypatch.setenv("UA_AGENTMAIL_INBOX_ADDRESS", "test@agentmail.to")

    fake_inbox = MagicMock()
    fake_inbox.inbox_id = "inbox_abc"

    fake_client = MagicMock()
    fake_client.inboxes.get = AsyncMock(return_value=fake_inbox)
    fake_client.inboxes.messages.send = AsyncMock(
        side_effect=RuntimeError("AgentMail upstream 503")
    )

    import sys
    import types
    fake_module = types.ModuleType("agentmail")
    fake_module.AsyncAgentMail = MagicMock(return_value=fake_client)
    monkeypatch.setitem(sys.modules, "agentmail", fake_module)

    svc = _SubprocessMailService()
    result = await svc.send_email(
        to="kevinjdragan@gmail.com",
        subject="test",
        text="hi",
    )
    assert result["status"] == "failed"
    assert "AgentMail upstream 503" in result.get("error", "")


@pytest.mark.asyncio
async def test_send_email_returns_skipped_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the agentmail package isn't installed, the shim must return
    skipped (not crash). Production has the SDK, but tests / dev boxes
    might not."""
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test-key-123")

    import sys
    # Force the import to fail by shadowing with a non-module sentinel.
    real_module = sys.modules.pop("agentmail", None)
    monkeypatch.setitem(sys.modules, "agentmail", None)
    try:
        svc = _SubprocessMailService()
        result = await svc.send_email(
            to="kevinjdragan@gmail.com", subject="test", text="hi",
        )
        assert result["status"] == "skipped"
    finally:
        if real_module is not None:
            sys.modules["agentmail"] = real_module
