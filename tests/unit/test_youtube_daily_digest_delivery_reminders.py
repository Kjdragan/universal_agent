"""Contract: when the digest email succeeds, the delivery-reminder
service is invoked exactly once with the right arguments; when it fails,
the reminder is never invoked.

These tests mirror the stubbing pattern in
``test_youtube_daily_digest_email_failures.py`` to keep the digest run
purely in-process and avoid any network/AgentMail/Telegram side effects.
"""
from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def digest_module(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_WORKSPACES_DIR", str(tmp_path))
    from universal_agent.scripts import youtube_daily_digest as ydd

    monkeypatch.setattr(ydd, "initialize_runtime_secrets", lambda: None)
    monkeypatch.setenv("MONDAY_YT_PLAYLIST", "PLfake")

    monkeypatch.setattr(
        ydd,
        "get_playlist_items",
        lambda playlist_id: [
            {"video_id": "vid1", "title": "First Video", "playlist_item_id": "pli-1"},
        ],
    )
    monkeypatch.setattr(
        ydd,
        "ingest_youtube_transcript",
        lambda video_url=None, video_id=None, require_proxy=True, **kwargs: {
            "ok": True,
            "transcript_text": "fake transcript content",
        },
    )

    async def _fake_generate(**kwargs) -> str:
        return "# Fake Digest\n\nSummary content.\n\n```json\n{\"decisions\": []}\n```"

    monkeypatch.setattr(ydd, "_generate_digest_content", _fake_generate)
    monkeypatch.setattr(ydd, "_save_repopulate_pocket", lambda **kw: None)
    monkeypatch.setattr(ydd, "_emit_csi_digest", lambda **kw: True)
    monkeypatch.setattr(
        ydd,
        "_save_tutorial_candidates",
        lambda **kw: tmp_path / "fake_candidates.json",
    )
    monkeypatch.setattr(ydd, "_dispatch_tutorial_candidates", lambda **kw: [])
    monkeypatch.setattr(ydd, "_save_processed_videos", lambda *a, **kw: None)
    # Don't shell out to the scratchpad publish script in tests: return a fake URL
    # so delivery takes the link-first path (no SSH, no WeasyPrint render).
    monkeypatch.setattr(
        ydd,
        "publish_html_to_scratch",
        lambda *a, **kw: "https://uaonvps.taildcc090.ts.net/scratch/test/digest.html",
    )
    return ydd


def _install_agentmail_stub(monkeypatch, digest_module, *, send_raises: bool = False):
    calls: list[dict[str, Any]] = []

    class _StubAgentMailService:
        async def startup(self):
            return None

        async def shutdown(self):
            return None

        async def send_email(self, **kwargs):
            calls.append(kwargs)
            if send_raises:
                raise RuntimeError("simulated SMTP failure")
            return {"message_id": "msg-test-1"}

    monkeypatch.setattr(digest_module, "AgentMailService", _StubAgentMailService)
    return calls


def _install_reminder_spy(monkeypatch, digest_module):
    """Replace the imported send_digest_delivery_reminder with a recording stub."""
    calls: list[dict[str, Any]] = []

    from universal_agent.services.digest_delivery_reminder import (
        DigestDeliveryReminderResult,
    )

    def _spy(**kwargs):
        calls.append(kwargs)
        return DigestDeliveryReminderResult(
            telegram_ok=True,
            telegram_message_id=42,
            telegram_error=None,
            dashboard_event_id="evt-fake-1",
            expires_at_iso="2026-05-19T12:44:00+00:00",
        )

    monkeypatch.setattr(digest_module, "send_digest_delivery_reminder", _spy)
    return calls


def test_reminder_fires_when_email_succeeds(monkeypatch, digest_module):
    _install_agentmail_stub(monkeypatch, digest_module, send_raises=False)
    reminder_calls = _install_reminder_spy(monkeypatch, digest_module)

    digest_module.process_daily_digest(
        dry_run=False,
        day_override="MONDAY",
        email_to="kevinjdragan@gmail.com",
        auto_tutorial_top_n=0,
    )

    assert len(reminder_calls) == 1, (
        "On a successful email, the digest must fire exactly one reminder"
    )
    call = reminder_calls[0]
    # Subject is "Daily YouTube Summaries — <date>" (date varies by run day),
    # so assert the stable prefix rather than the day-specific tail. The day
    # name was intentionally dropped from the subject (2026-05-29) because the
    # cron digests the *prior* day's playlist, which made the old day-named
    # subject mismatch the delivery day.
    assert call["subject"].startswith("Daily YouTube Summaries — ")
    assert call["recipient"] == "kevinjdragan@gmail.com"
    assert "sent_at_utc" in call


def test_reminder_never_fires_when_email_fails(monkeypatch, digest_module):
    _install_agentmail_stub(monkeypatch, digest_module, send_raises=True)
    reminder_calls = _install_reminder_spy(monkeypatch, digest_module)
    monkeypatch.setattr(
        digest_module, "_emit_proactive_delivery_failure", lambda **kw: None,
    )

    digest_module.process_daily_digest(
        dry_run=False,
        day_override="MONDAY",
        email_to="kevinjdragan@gmail.com",
        auto_tutorial_top_n=0,
    )

    assert reminder_calls == [], (
        "When AgentMail raises, the reminder service must not be called — "
        "we don't surface 'delivered' signals for an undelivered digest."
    )


def test_reminder_never_fires_when_email_to_is_none(monkeypatch, digest_module):
    """no-email mode (caller drives delivery elsewhere) must not fire a
    reminder either — there's nothing to notify about."""
    _install_agentmail_stub(monkeypatch, digest_module, send_raises=False)
    reminder_calls = _install_reminder_spy(monkeypatch, digest_module)

    digest_module.process_daily_digest(
        dry_run=False,
        day_override="MONDAY",
        email_to=None,
        auto_tutorial_top_n=0,
    )

    assert reminder_calls == [], (
        "no-email mode (email_to=None) must not fire the delivery reminder"
    )


def test_reminder_failure_does_not_break_digest(monkeypatch, digest_module):
    """If the reminder service itself raises (e.g. Telegram outage AND
    DB write failure), the digest must continue to mark videos processed.
    This protects the digest's primary contract."""
    _install_agentmail_stub(monkeypatch, digest_module, send_raises=False)
    save_calls: list[Any] = []
    monkeypatch.setattr(
        digest_module,
        "_save_processed_videos",
        lambda items, day: save_calls.append((items, day)),
    )

    def _explode(**kwargs):
        raise RuntimeError("reminder service blew up")

    monkeypatch.setattr(digest_module, "send_digest_delivery_reminder", _explode)

    digest_module.process_daily_digest(
        dry_run=False,
        day_override="MONDAY",
        email_to="kevinjdragan@gmail.com",
        auto_tutorial_top_n=0,
    )

    # Email succeeded → videos must still be saved as processed regardless
    # of reminder-side failure.
    assert len(save_calls) == 1, (
        "Reminder failure must not block the digest from marking videos processed"
    )
