"""Regression tests for proactive intelligence report email delivery.

Locks the 2026-06-04 fix (S1): deliver_intelligence_report must only report
``email_sent=True`` when the mailer actually returned a non-empty
``message_id`` on the wire. The prior code set ``email_sent = True``
unconditionally right after the send call, so a no-op ``_DummyMail`` that
returned ``{"status": "skipped", "message_id": ""}`` made 0 sent reports look
"(emailed)" for weeks. It also asserts the FYI/DIGEST identity tags are passed.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services import llm_classifier
from universal_agent.services import proactive_intelligence_report as pir


class _RecordingMail:
    """Mailer stub that records send_email kwargs and returns a fixed result."""

    def __init__(self, result: dict) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _connect(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "activity_state.db")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def _stub_compose(monkeypatch):
    """Stub out the LLM-backed compose + formatting so the test is hermetic and
    only exercises the delivery / email_sent bookkeeping."""

    async def _compose(conn, period=None):
        return {
            "period": "test",
            "timestamp": "2026-06-04T00:00:00+00:00",
            "stats": {"x": 1},
            "analysis": "analysis body",
            "recommendations": "do the thing",
        }

    monkeypatch.setattr(pir, "compose_intelligence_report", _compose)
    monkeypatch.setattr(
        pir, "format_report_email", lambda report: ("subject", "text body", "<html>body</html>")
    )


@pytest.mark.asyncio
async def test_email_sent_true_only_with_message_id(tmp_path, _stub_compose):
    conn = _connect(tmp_path)
    mail = _RecordingMail({"status": "sent", "message_id": "msg-real-1", "thread_id": "t1"})

    result = await pir.deliver_intelligence_report(
        conn=conn, mail_service=mail, recipient="kevinjdragan@gmail.com"
    )

    assert result["email_sent"] is True
    assert result["email_message_id"] == "msg-real-1"
    assert result["stored_for_dashboard"] is True

    row = conn.execute(
        "SELECT email_message_id FROM proactive_intelligence_reports"
    ).fetchone()
    assert row["email_message_id"] == "msg-real-1"

    # Identity tags must survive — a proactive report is FYI/DIGEST.
    assert mail.calls[0]["action"] == pir.ActionTag.FYI
    assert mail.calls[0]["kind"] == pir.KindTag.DIGEST
    assert mail.calls[0]["source"] == "proactive_report cron"


@pytest.mark.asyncio
async def test_dummy_skip_return_does_not_claim_delivery(tmp_path, _stub_compose):
    # The exact shape the old _DummyMail returned — empty message_id.
    conn = _connect(tmp_path)
    mail = _RecordingMail({"status": "skipped", "message_id": "", "thread_id": ""})

    result = await pir.deliver_intelligence_report(
        conn=conn, mail_service=mail, recipient="kevinjdragan@gmail.com"
    )

    # The regression: an empty message_id must NEVER flip email_sent to True.
    assert result["email_sent"] is False
    assert result["email_message_id"] == ""
    # The report is still composed + stored for the dashboard.
    assert result["stored_for_dashboard"] is True
    row = conn.execute(
        "SELECT email_message_id FROM proactive_intelligence_reports"
    ).fetchone()
    assert row["email_message_id"] == ""


@pytest.mark.asyncio
async def test_none_return_does_not_claim_delivery(tmp_path, _stub_compose):
    conn = _connect(tmp_path)
    mail = _RecordingMail(None)  # mailer returned nothing at all

    result = await pir.deliver_intelligence_report(
        conn=conn, mail_service=mail, recipient="kevinjdragan@gmail.com"
    )

    assert result["email_sent"] is False
    assert result["email_message_id"] == ""


@pytest.mark.asyncio
async def test_send_exception_leaves_email_sent_false(tmp_path, _stub_compose):
    conn = _connect(tmp_path)

    class _Boom:
        async def send_email(self, **kwargs):
            raise RuntimeError("429 daily send limit, hard fail")

    result = await pir.deliver_intelligence_report(
        conn=conn, mail_service=_Boom(), recipient="kevinjdragan@gmail.com"
    )

    assert result["email_sent"] is False
    assert result["email_message_id"] == ""
    # A send failure must not block the dashboard store.
    assert result["stored_for_dashboard"] is True


# ── _call_reasoning_llm: routed to ZAI glm-4.5-air (2026-06-05) ──────────────
# The reasoning pass moved off a direct google-genai gemini-2.0-flash call
# (whose API key was 403 API_KEY_SERVICE_BLOCKED) onto the central ZAI-backed
# _call_llm helper, pinned to the haiku-equivalent glm-4.5-air. These lock the
# model pin + the deterministic fallback.


@pytest.mark.asyncio
async def test_reasoning_llm_uses_glm_4_5_air(monkeypatch):
    captured: dict = {}

    async def _fake_call_llm(*, system, user, model=None, max_tokens=1024):
        captured["model"] = model
        captured["user"] = user
        return "  Colleague-style analysis of the pipeline.  "

    monkeypatch.setattr(
        llm_classifier, "_call_llm", _fake_call_llm
    )

    out = await pir._call_reasoning_llm({"proactive_tasks": {"completed": 2}}, "afternoon")

    assert out == "Colleague-style analysis of the pipeline."  # stripped
    assert captured["model"] == "glm-4.5-air"  # haiku-equivalent, explicitly pinned
    assert "afternoon" in captured["user"]


@pytest.mark.asyncio
async def test_reasoning_llm_falls_back_on_exception(monkeypatch):
    async def _boom(*, system, user, model=None, max_tokens=1024):
        raise RuntimeError("zai unreachable")

    monkeypatch.setattr(llm_classifier, "_call_llm", _boom)

    stats = {
        "proactive_tasks": {"completed": 2, "open": 1, "failed": 0},
        "budget": {"used": 3, "daily_limit": 10},
    }
    out = await pir._call_reasoning_llm(stats, "afternoon")

    assert "Proactive pipeline afternoon summary" in out
    assert "LLM analysis unavailable" in out


@pytest.mark.asyncio
async def test_reasoning_llm_empty_response_falls_back(monkeypatch):
    async def _empty(*, system, user, model=None, max_tokens=1024):
        return "   "

    monkeypatch.setattr(llm_classifier, "_call_llm", _empty)

    out = await pir._call_reasoning_llm({"proactive_tasks": {}, "budget": {}}, "morning")
    assert "Proactive pipeline morning summary" in out
