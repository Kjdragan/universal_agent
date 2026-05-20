"""Unit tests for the digest delivery-reminder service.

Hard contracts pinned here:

  1. On a successful AgentMail send, both signals fire — a Telegram
     message (captured via stub) AND a dashboard notification row in
     the activity DB with expires_at set to now + TTL.

  2. Neither fires when the digest email never succeeds (this is
     covered transitively in test_youtube_daily_digest_email_failures.py
     — the digest script never calls into us on failure).

  3. TTL math is correct: expires_at = sent_at + ttl_minutes.

  4. Telegram delivery failure does NOT block the dashboard notification.

  5. No network is touched by the test (Telegram + AgentMail both stubbed).

  6. The Telegram dismissal schedule row is recorded so the gateway sweep
     can pick it up later — but the test never calls Telegram's
     deleteMessage itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest


@pytest.fixture
def reminder_env(monkeypatch, tmp_path):
    """Wire env so the service writes to a tmp activity DB."""
    db_path = tmp_path / "activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-used-network-stubbed")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999111")
    # Force a deterministic TTL for these tests
    monkeypatch.setenv("UA_DIGEST_REMINDER_TTL_MINUTES", "90")
    return {"db_path": str(db_path)}


def _install_telegram_stub(monkeypatch, *, ok: bool = True, message_id: int = 42, err: str = "ok"):
    captured = {"calls": []}

    def fake_send(chat_id, text, **kwargs):
        captured["calls"].append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        if ok:
            payload = {"ok": True, "result": {"message_id": message_id, "chat": {"id": chat_id}}}
            return True, payload, "ok"
        return False, None, err

    # Patch at the import site inside the service module.
    monkeypatch.setattr(
        "universal_agent.services.digest_delivery_reminder.telegram_send_with_response_sync",
        fake_send,
    )
    return captured


def test_reminder_fires_both_channels_on_success(reminder_env, monkeypatch):
    captured = _install_telegram_stub(monkeypatch, ok=True, message_id=12345)

    from universal_agent.services.digest_delivery_reminder import (
        KIND_DAILY_DIGEST_DELIVERED,
        send_digest_delivery_reminder,
    )
    sent_at = datetime(2026, 5, 19, 11, 14, 0, tzinfo=timezone.utc)  # 6:14 AM CDT
    result = send_digest_delivery_reminder(
        subject="Daily YouTube Digest: Monday",
        recipient="kevinjdragan@gmail.com",
        sent_at_utc=sent_at,
        gmail_thread_url="https://mail.google.com/mail/u/0/#inbox/abc",
    )

    # --- Telegram side ---
    assert result.telegram_ok is True
    assert result.telegram_message_id == 12345
    assert result.telegram_error is None
    assert len(captured["calls"]) == 1
    call = captured["calls"][0]
    assert call["chat_id"] == "999111"
    assert "Daily YouTube Digest delivered" in call["text"]
    assert "kevinjdragan@gmail.com" in call["text"]
    assert "https://mail.google.com" in call["text"]

    # --- Dashboard side ---
    assert result.dashboard_event_id is not None
    expected_expiry = (sent_at + timedelta(minutes=90)).isoformat()
    assert result.expires_at_iso == expected_expiry

    # Row in activity_events
    conn = sqlite3.connect(reminder_env["db_path"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM activity_events WHERE kind = ?",
        (KIND_DAILY_DIGEST_DELIVERED,),
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["expires_at"] == expected_expiry
    assert row["severity"] == "success"
    assert "kevinjdragan@gmail.com" in (row["full_message"] or "")
    # Houston time appears (not UTC) in the body
    assert "CDT" in (row["full_message"] or "") or "CST" in (row["full_message"] or "")

    # Telegram dismissal row scheduled
    sched = conn.execute(
        "SELECT chat_id, message_id, dismiss_at FROM digest_telegram_reminders"
    ).fetchall()
    assert len(sched) == 1
    assert sched[0]["chat_id"] == "999111"
    assert sched[0]["message_id"] == 12345
    assert sched[0]["dismiss_at"] == expected_expiry
    conn.close()


def test_telegram_failure_does_not_block_dashboard(reminder_env, monkeypatch):
    _install_telegram_stub(monkeypatch, ok=False, err="telegram_http_403")

    from universal_agent.services.digest_delivery_reminder import (
        KIND_DAILY_DIGEST_DELIVERED,
        send_digest_delivery_reminder,
    )
    result = send_digest_delivery_reminder(
        subject="Daily YouTube Digest: Tuesday",
        recipient="kevinjdragan@gmail.com",
    )

    assert result.telegram_ok is False
    assert result.telegram_error == "telegram_http_403"
    assert result.telegram_message_id is None
    # Dashboard tile still fires
    assert result.dashboard_event_id is not None

    conn = sqlite3.connect(reminder_env["db_path"])
    try:
        rows = conn.execute(
            "SELECT id FROM activity_events WHERE kind = ?",
            (KIND_DAILY_DIGEST_DELIVERED,),
        ).fetchall()
        assert len(rows) == 1
        # No dismissal row scheduled (no message_id to delete).  The
        # reminders table may not exist yet if no successful send has
        # ever scheduled one — both shapes (empty table or absent table)
        # represent "no dismissal pending".
        try:
            sched = conn.execute(
                "SELECT id FROM digest_telegram_reminders"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            assert "no such table" in str(exc).lower()
            sched = []
        assert sched == []
    finally:
        conn.close()


def test_no_telegram_chat_id_skips_telegram_but_still_dashboards(reminder_env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("UA_OPERATOR_TELEGRAM_CHAT_ID", raising=False)
    captured = _install_telegram_stub(monkeypatch, ok=True)

    from universal_agent.services.digest_delivery_reminder import (
        send_digest_delivery_reminder,
    )
    result = send_digest_delivery_reminder(
        subject="Daily YouTube Digest: Wednesday",
        recipient="kevinjdragan@gmail.com",
    )

    assert result.telegram_ok is False
    assert result.telegram_error == "telegram_chat_id_not_configured"
    # No Telegram call happened
    assert captured["calls"] == []
    # Dashboard still wrote the tile
    assert result.dashboard_event_id is not None


def test_ttl_minutes_override_wins(reminder_env, monkeypatch):
    _install_telegram_stub(monkeypatch, ok=True, message_id=7)
    from universal_agent.services.digest_delivery_reminder import (
        send_digest_delivery_reminder,
    )
    sent_at = datetime(2026, 5, 19, 11, 0, 0, tzinfo=timezone.utc)
    result = send_digest_delivery_reminder(
        subject="X",
        recipient="x@example.com",
        sent_at_utc=sent_at,
        ttl_minutes=15,
    )
    expected = (sent_at + timedelta(minutes=15)).isoformat()
    assert result.expires_at_iso == expected


def test_ttl_env_var_used_when_no_override(reminder_env, monkeypatch):
    monkeypatch.setenv("UA_DIGEST_REMINDER_TTL_MINUTES", "30")
    _install_telegram_stub(monkeypatch, ok=True, message_id=7)
    from universal_agent.services.digest_delivery_reminder import (
        send_digest_delivery_reminder,
    )
    sent_at = datetime(2026, 5, 19, 11, 0, 0, tzinfo=timezone.utc)
    result = send_digest_delivery_reminder(
        subject="X",
        recipient="x@example.com",
        sent_at_utc=sent_at,
    )
    expected = (sent_at + timedelta(minutes=30)).isoformat()
    assert result.expires_at_iso == expected


def test_houston_time_formatting_never_utc(reminder_env, monkeypatch):
    _install_telegram_stub(monkeypatch, ok=True, message_id=7)
    from universal_agent.services.digest_delivery_reminder import (
        send_digest_delivery_reminder,
    )
    # 11:14 UTC on 2026-05-19 == 6:14 AM CDT on 2026-05-19 Tue
    sent_at = datetime(2026, 5, 19, 11, 14, 0, tzinfo=timezone.utc)
    send_digest_delivery_reminder(
        subject="X",
        recipient="x@example.com",
        sent_at_utc=sent_at,
    )
    conn = sqlite3.connect(reminder_env["db_path"])
    try:
        row = conn.execute(
            "SELECT full_message FROM activity_events WHERE kind = ?",
            ("daily_digest_delivered",),
        ).fetchone()
        msg = row[0]
        # Must mention Houston-local hour (6), not UTC hour (11)
        assert "6:14" in msg
        # Must NOT include the UTC zone marker for the rendered Sent: line
        # (the metadata JSON column may have an ISO UTC timestamp; that's fine,
        # we only inspect the human-readable line here.)
        sent_line = [ln for ln in msg.splitlines() if ln.startswith("Sent:")]
        assert sent_line, "expected a Sent: line in the body"
        assert "UTC" not in sent_line[0]
    finally:
        conn.close()
