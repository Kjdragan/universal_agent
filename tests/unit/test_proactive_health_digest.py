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
    _format_digest_email,
    _operator_plain_summary,
    _synthesize_plain_lead,
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


def test_digest_leads_with_plain_language_then_technical_detail():
    """Email leads with a plain-language summary the operator can act on,
    then the technical detail (for Claude) below a divider."""
    finding = {
        "finding_id": "invariant:disk_usage_health",
        "metric_key": "disk_usage_health",
        "severity": "critical",
        "title": "Disk usage across monitored mounts within safe range",
        "recommendation": "Disk pressure on 3 mount(s). Worst 91.3%. <technical...>",
        "runbook_command": "df -h",
        "observed_value": {"worst_used_pct": 91.3},
        "metadata": {
            "operator_summary": (
                "The production server's disk is 91% full and slowly filling up."
            )
        },
    }
    _, body = _format_digest_email([finding], "2026-06-26T00:20:34Z")

    # Plain lead present, and BEFORE the technical section.
    assert "IN PLAIN LANGUAGE" in body
    assert "WHAT YOU CAN DO" in body
    assert "TECHNICAL DETAIL" in body
    assert body.index("IN PLAIN LANGUAGE") < body.index("TECHNICAL DETAIL")
    # The plain-English operator_summary leads; the misleading "within safe
    # range" title only appears in the technical block (after the divider).
    plain, _, technical = body.partition("TECHNICAL DETAIL")
    assert "disk is 91% full" in plain
    assert "within safe range" not in plain
    assert "within safe range" in technical
    # Technical specificity is preserved for the handoff.
    assert "metric_key=disk_usage_health" in technical
    assert "df -h" in technical


def test_format_digest_uses_llm_lead_over_deterministic_summaries():
    """When an LLM lead is supplied, it replaces the per-finding deterministic
    summaries in the plain section (technical detail unaffected)."""
    finding = {
        "finding_id": "invariant:disk_usage_health",
        "metric_key": "disk_usage_health",
        "severity": "critical",
        "title": "Disk usage across monitored mounts within safe range",
        "recommendation": "Disk pressure ... <technical>",
        "runbook_command": "df -h",
        "observed_value": {"worst_used_pct": 91.3},
        "metadata": {"operator_summary": "DETERMINISTIC_FALLBACK_TEXT"},
    }
    llm_lead = "The server is almost out of disk space — about 17 GB left."
    _, body = _format_digest_email([finding], "2026-06-26T00:20:34Z", plain_lead=llm_lead)
    plain, _, technical = body.partition("TECHNICAL DETAIL")
    assert llm_lead in plain
    assert "DETERMINISTIC_FALLBACK_TEXT" not in plain  # LLM lead won
    assert "metric_key=disk_usage_health" in technical  # technical preserved


@pytest.mark.asyncio
async def test_synthesize_plain_lead_falls_back_to_none_on_failure(monkeypatch):
    """Any LLM error → None (caller uses the deterministic lead); never raises."""
    async def _boom(*a, **k):
        raise RuntimeError("zai down")

    monkeypatch.setattr(notifier, "_call_llm", _boom, raising=False)
    # _call_llm is imported function-locally; patch at its source module too.
    import universal_agent.services.llm_classifier as llmc

    monkeypatch.setattr(llmc, "_call_llm", _boom, raising=False)
    out = await _synthesize_plain_lead(_criticals(1), "t")
    assert out is None


@pytest.mark.asyncio
async def test_synthesize_plain_lead_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("UA_HEARTBEAT_PROACTIVE_HEALTH_LLM_LEAD", "0")
    out = await _synthesize_plain_lead(_criticals(1), "t")
    assert out is None


def test_operator_plain_summary_falls_back_when_unauthored():
    """No operator_summary → first sentence of the recommendation; then title."""
    assert _operator_plain_summary(
        {"recommendation": "First sentence here. Second sentence.", "title": "T"}
    ) == "First sentence here."
    assert _operator_plain_summary({"title": "Only a title"}) == "Only a title"
    assert (
        _operator_plain_summary(
            {"metadata": {"operator_summary": "Plain lead."}, "recommendation": "tech"}
        )
        == "Plain lead."
    )


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
