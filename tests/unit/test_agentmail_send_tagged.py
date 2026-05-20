"""Integration test for AgentMailService.send_email with tag parameters.

Verifies that when both ``action`` and ``kind`` are supplied, the subject
landed at the AgentMail client carries the ``[ACTION/KIND]`` prefix and the
body has the tag banner prepended. Also verifies that omitting the tag
params preserves backward compatibility (no prefix, no banner).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from universal_agent.services.email_tags import ActionTag, KindTag

_BASE_ENV = {
    "UA_AGENTMAIL_ENABLED": "1",
    "AGENTMAIL_API_KEY": "test-api-key-123",
    "UA_AGENTMAIL_INBOX_ADDRESS": "simone@testdomain.com",
    "UA_AGENTMAIL_AUTO_SEND": "1",  # force-send path
    "UA_AGENTMAIL_WS_ENABLED": "0",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("UA_AGENTMAIL_INBOX_ADDRESSES", raising=False)
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))


@pytest.fixture
def mock_client():
    client = MagicMock()

    mock_inbox = MagicMock()
    mock_inbox.inbox_id = "simone@testdomain.com"
    client.inboxes.get = AsyncMock(return_value=mock_inbox)
    client.inboxes.create = AsyncMock(return_value=mock_inbox)

    mock_msg = MagicMock()
    mock_msg.message_id = "msg_test_tag_001"
    client.inboxes.messages.send = AsyncMock(return_value=mock_msg)

    mock_draft = MagicMock()
    mock_draft.draft_id = "drf_test_tag_001"
    client.inboxes.drafts.create = AsyncMock(return_value=mock_draft)

    return client


@pytest.fixture
async def service(mock_client):
    from universal_agent.services.agentmail_service import AgentMailService

    svc = AgentMailService(
        dispatch_fn=AsyncMock(return_value=(True, "agent")),
        notification_sink=MagicMock(),
    )
    svc._client = mock_client
    await svc._ensure_inbox()
    svc._enabled = True
    svc._started = True
    yield svc
    await svc.shutdown()


# ---------------------------------------------------------------------------
# Tagged path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_with_tags_prefixes_subject_and_banner(service, mock_client):
    await service.send_email(
        to="kevin@example.com",
        subject="Daily YouTube Digest: Monday",
        text="raw body content",
        html="<p>raw body content</p>",
        action=ActionTag.FYI,
        kind=KindTag.DIGEST,
        source="youtube_daily_digest cron",
        related=["day=monday"],
        force_send=True,
    )

    call = mock_client.inboxes.messages.send.await_args
    assert call is not None, "expected the AgentMail client to receive a send call"
    kwargs = call.kwargs
    assert kwargs["subject"] == "[FYI/DIGEST] Daily YouTube Digest: Monday"
    # Plaintext body has the banner up top
    assert kwargs["text"].startswith("Tags: FYI/DIGEST")
    assert "Source: youtube_daily_digest cron" in kwargs["text"]
    assert "Related: day=monday" in kwargs["text"]
    assert "raw body content" in kwargs["text"]
    # HTML body gets the html banner block
    assert "FYI/DIGEST" in kwargs["html"]
    assert "<p>raw body content</p>" in kwargs["html"]


@pytest.mark.asyncio
async def test_send_email_string_tag_inputs_are_accepted(service, mock_client):
    await service.send_email(
        to="kevin@example.com",
        subject="hello",
        text="body",
        action="ACTION",
        kind="INCIDENT",
        source="ci-watcher",
        force_send=True,
    )

    call = mock_client.inboxes.messages.send.await_args
    assert call.kwargs["subject"] == "[ACTION/INCIDENT] hello"


@pytest.mark.asyncio
async def test_send_email_bad_tag_raises(service, mock_client):
    with pytest.raises(ValueError):
        await service.send_email(
            to="kevin@example.com",
            subject="hello",
            text="body",
            action="URGENT",  # not in the enum
            kind=KindTag.INCIDENT,
            source="ci-watcher",
            force_send=True,
        )
    # And nothing was actually sent.
    mock_client.inboxes.messages.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Backward-compat path (un-migrated callsites)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_without_tags_is_unchanged(service, mock_client):
    """A caller that omits action/kind should see no behavioral diff."""
    await service.send_email(
        to="kevin@example.com",
        subject="Plain subject",
        text="plain body",
        html="<p>plain</p>",
        force_send=True,
    )

    call = mock_client.inboxes.messages.send.await_args
    assert call.kwargs["subject"] == "Plain subject"
    assert call.kwargs["text"] == "plain body"
    assert call.kwargs["html"] == "<p>plain</p>"


@pytest.mark.asyncio
async def test_send_email_partial_tag_does_not_apply(service, mock_client):
    """Passing only one of action/kind is treated as un-tagged."""
    await service.send_email(
        to="kevin@example.com",
        subject="Half-tagged",
        text="body",
        action=ActionTag.FYI,  # kind missing
        force_send=True,
    )
    call = mock_client.inboxes.messages.send.await_args
    assert call.kwargs["subject"] == "Half-tagged"
    assert call.kwargs["text"] == "body"


# ---------------------------------------------------------------------------
# Idempotency at the wrapper layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_does_not_double_prefix_when_caller_pretagged(service, mock_client):
    """If the caller pre-tags the subject, the wrapper must not double-tag."""
    await service.send_email(
        to="kevin@example.com",
        subject="[FYI/DIGEST] Daily YouTube Digest: Monday",
        text="body",
        action=ActionTag.FYI,
        kind=KindTag.DIGEST,
        source="youtube_daily_digest cron",
        force_send=True,
    )
    call = mock_client.inboxes.messages.send.await_args
    assert call.kwargs["subject"] == "[FYI/DIGEST] Daily YouTube Digest: Monday"
