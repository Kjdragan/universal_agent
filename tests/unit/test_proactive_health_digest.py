"""Unit tests for the proactive_health critical DIGEST send path (S5 Phase C).

The in-process notifier sent one email per critical finding; the deploy-
independent timer collapses all current criticals into a SINGLE digest with the
INCIDENT/ACTION identity, acquiring + shutting down a fresh mailer when run in a
oneshot subprocess.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from universal_agent.services import proactive_health_notifier as notifier
from universal_agent.services.email_tags import ActionTag, KindTag
from universal_agent.services.proactive_health_notifier import (
    KEVIN_EMAIL,
    send_critical_digest,
)


def _criticals(n: int = 2) -> list[dict]:
    return [
        {
            "finding_id": f"invariant:c{i}",
            "metric_key": f"metric_{i}",
            "severity": "critical",
            "title": f"Critical {i}",
            "recommendation": f"fix {i}",
            "runbook_command": f"run {i}",
            "observed_value": {"x": i},
        }
        for i in range(n)
    ]


@pytest.fixture
def fake_agentmail():
    mock = AsyncMock()
    mock.send_email = AsyncMock(return_value={"message_id": "digest-1", "status": "sent"})
    mock.shutdown = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_digest_sends_single_email_for_multiple_criticals(fake_agentmail):
    result = await send_critical_digest(
        criticals=_criticals(3),
        generated_at="2026-06-05T00:00:00+00:00",
        agentmail_service=fake_agentmail,
    )
    assert result["sent"] is True
    assert result["message_id"] == "digest-1"
    # ONE email, not three.
    fake_agentmail.send_email.assert_awaited_once()
    call = fake_agentmail.send_email.call_args
    assert call.kwargs["to"] == KEVIN_EMAIL
    assert call.kwargs["force_send"] is True
    assert call.kwargs["action"] == ActionTag.ACTION
    assert call.kwargs["kind"] == KindTag.INCIDENT
    assert call.kwargs["source"] == "proactive_health_timer"
    assert "3 critical" in call.kwargs["subject"]
    body = call.kwargs["text"]
    assert "metric_0" in body and "metric_1" in body and "metric_2" in body
    # A passed-in handle is NOT owned → not shut down.
    fake_agentmail.shutdown.assert_not_called()


@pytest.mark.asyncio
async def test_digest_no_criticals_is_noop(fake_agentmail):
    result = await send_critical_digest(
        criticals=[], generated_at="t", agentmail_service=fake_agentmail
    )
    assert result["sent"] is False
    assert result["reason"] == "no_criticals"
    fake_agentmail.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_digest_send_failure_does_not_raise(fake_agentmail):
    fake_agentmail.send_email.side_effect = RuntimeError("smtp down")
    result = await send_critical_digest(
        criticals=_criticals(1), generated_at="t", agentmail_service=fake_agentmail
    )
    assert result["sent"] is False
    assert "smtp down" in result["reason"]


@pytest.mark.asyncio
async def test_digest_constructs_and_shuts_down_owned_handle(monkeypatch):
    fresh = AsyncMock()
    fresh.send_email = AsyncMock(return_value={"message_id": "fresh-d"})
    fresh.shutdown = AsyncMock()
    monkeypatch.setattr(notifier, "_resolve_agentmail_service_via_gateway", lambda: None)

    async def _construct():
        return fresh

    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _construct)
    result = await send_critical_digest(criticals=_criticals(1), generated_at="t")
    assert result["sent"] is True
    fresh.send_email.assert_awaited_once()
    fresh.shutdown.assert_awaited_once()  # owned handle cleaned up in finally


@pytest.mark.asyncio
async def test_digest_no_mailer_returns_unsent(monkeypatch):
    monkeypatch.setattr(notifier, "_resolve_agentmail_service_via_gateway", lambda: None)

    async def _none():
        return None

    monkeypatch.setattr(notifier, "_construct_started_agentmail_service", _none)
    result = await send_critical_digest(criticals=_criticals(1), generated_at="t")
    assert result["sent"] is False
    assert "agentmail_service=None" in result["reason"]
