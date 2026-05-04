"""Async email/Telegram dispatcher for high-severity notifications.

Today, `_add_notification` writes records to `activity_events` with
`channels_json=["dashboard","email","telegram"]` but only the dashboard
channel has a consumer.  Email/Telegram fields exist on the schema with
no out-of-band delivery — the user has to look at the dashboard to see
alerts.

`NotificationDispatcher` closes that gap.  It polls the activity store
on an interval, finds undelivered error/warning notifications, sends
them via the provided email + Telegram functions, and marks each row
delivered so a restart does not re-blast historical state.

These tests pin the contract:

  1. Error/warning rows are delivered to email + Telegram.
  2. Info rows are NOT delivered (channel surface is dashboard-only).
  3. Already-delivered rows are skipped on subsequent ticks.
  4. A flapping kind is rate-limited via a per-kind cooldown.
  5. Email send failure does NOT stop the loop and does NOT mark
     the row delivered (it stays eligible for next tick).
  6. Telegram failure follows the same isolation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from universal_agent.services.notification_dispatcher import NotificationDispatcher


def _row(
    *,
    event_id: str,
    kind: str = "cron_run_failed",
    severity: str = "error",
    title: str = "Test Alert",
    message: str = "Something broke.",
    metadata: dict | None = None,
    channels: list[str] | None = None,
    created_at: str = "2026-05-04T10:00:00+00:00",
    updated_at: str = "2026-05-04T10:00:00+00:00",
) -> dict:
    return {
        "id": event_id,
        "kind": kind,
        "severity": severity,
        "title": title,
        "full_message": message,
        "summary": message,
        "metadata": metadata or {},
        "channels": channels or ["dashboard", "email", "telegram"],
        "email_targets": ["test@example.com"],
        "created_at": created_at,
        "updated_at": updated_at,
    }


class _Recorder:
    def __init__(self, *, fail: bool = False):
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("simulated send failure")
        return True


@pytest.mark.asyncio
async def test_dispatcher_delivers_error_to_email_and_telegram():
    rows = [_row(event_id="ntf_err_1")]
    delivered: list[tuple[str, str]] = []
    email = _Recorder()
    telegram = _Recorder()

    dispatcher = NotificationDispatcher(
        get_pending_rows=lambda: list(rows),
        mark_delivered=lambda event_id, channel: delivered.append((event_id, channel)),
        send_email=email,
        send_telegram=telegram,
        email_targets=["test@example.com"],
        telegram_chat_id="1234",
        now_fn=lambda: time.time(),
    )

    summary = await dispatcher.dispatch_pending_once()

    assert summary["email_sent"] == 1
    assert summary["telegram_sent"] == 1
    assert ("ntf_err_1", "email") in delivered
    assert ("ntf_err_1", "telegram") in delivered
    assert email.calls and email.calls[0]["to"] == "test@example.com"
    assert telegram.calls and "Test Alert" in telegram.calls[0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_skips_info_severity_rows():
    rows = [_row(event_id="ntf_info_1", severity="info")]
    delivered: list[tuple[str, str]] = []
    email = _Recorder()
    telegram = _Recorder()

    dispatcher = NotificationDispatcher(
        get_pending_rows=lambda: list(rows),
        mark_delivered=lambda event_id, channel: delivered.append((event_id, channel)),
        send_email=email,
        send_telegram=telegram,
        email_targets=["test@example.com"],
        telegram_chat_id="1234",
        now_fn=lambda: time.time(),
    )

    summary = await dispatcher.dispatch_pending_once()

    assert summary["email_sent"] == 0
    assert summary["telegram_sent"] == 0
    assert delivered == []
    assert email.calls == []
    assert telegram.calls == []


@pytest.mark.asyncio
async def test_dispatcher_skips_already_delivered_rows():
    """Row metadata indicates email + telegram both already delivered."""
    rows = [
        _row(
            event_id="ntf_done_1",
            metadata={
                "delivery": {
                    "email": {"delivered_at": "2026-05-04T10:00:00+00:00"},
                    "telegram": {"delivered_at": "2026-05-04T10:00:00+00:00"},
                },
            },
            updated_at="2026-05-04T10:00:00+00:00",
        )
    ]
    email = _Recorder()
    telegram = _Recorder()

    dispatcher = NotificationDispatcher(
        get_pending_rows=lambda: list(rows),
        mark_delivered=lambda *args: None,
        send_email=email,
        send_telegram=telegram,
        email_targets=["test@example.com"],
        telegram_chat_id="1234",
        now_fn=lambda: time.time(),
    )

    summary = await dispatcher.dispatch_pending_once()

    assert summary["email_sent"] == 0
    assert summary["telegram_sent"] == 0
    assert email.calls == []
    assert telegram.calls == []


@pytest.mark.asyncio
async def test_dispatcher_cooldown_prevents_flapping_kind_spam():
    """Two rows of the same kind delivered within the cooldown window
    should only produce one set of sends — protects email/Telegram
    from a flapping cron job."""
    rows = [
        _row(event_id="ntf_a", kind="cron_run_failed"),
        _row(event_id="ntf_b", kind="cron_run_failed"),
    ]
    email = _Recorder()
    telegram = _Recorder()

    dispatcher = NotificationDispatcher(
        get_pending_rows=lambda: list(rows),
        mark_delivered=lambda *args: None,
        send_email=email,
        send_telegram=telegram,
        email_targets=["test@example.com"],
        telegram_chat_id="1234",
        cooldown_seconds=600,
        now_fn=lambda: time.time(),
    )

    await dispatcher.dispatch_pending_once()

    assert len(email.calls) == 1, f"cooldown should suppress 2nd kind=cron_run_failed; got {len(email.calls)} sends"
    assert len(telegram.calls) == 1


@pytest.mark.asyncio
async def test_dispatcher_email_failure_does_not_mark_delivered():
    rows = [_row(event_id="ntf_err_2")]
    delivered: list[tuple[str, str]] = []
    email = _Recorder(fail=True)
    telegram = _Recorder()

    dispatcher = NotificationDispatcher(
        get_pending_rows=lambda: list(rows),
        mark_delivered=lambda event_id, channel: delivered.append((event_id, channel)),
        send_email=email,
        send_telegram=telegram,
        email_targets=["test@example.com"],
        telegram_chat_id="1234",
        now_fn=lambda: time.time(),
    )

    summary = await dispatcher.dispatch_pending_once()

    assert summary["email_failed"] == 1
    assert summary["email_sent"] == 0
    assert ("ntf_err_2", "email") not in delivered, (
        "Failed email send must NOT mark delivered — row stays eligible for retry."
    )
    # Telegram should still have been attempted (failure isolation per channel).
    assert summary["telegram_sent"] == 1
    assert ("ntf_err_2", "telegram") in delivered


@pytest.mark.asyncio
async def test_mark_delivered_helper_writes_metadata_via_json_set(tmp_path, monkeypatch):
    """The activity-store glue must persist delivery state on the
    correct row without overwriting the rest of metadata."""
    from universal_agent import gateway_server

    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setattr(gateway_server, "_notifications", [])

    # Seed an activity row via the canonical write path.
    notif = gateway_server._add_notification(
        kind="cron_run_failed",
        title="seed",
        message="seed message",
        severity="error",
        metadata={"job_id": "seed_job", "error": "seed error"},
    )
    event_id = str(notif.get("id") or "")
    assert event_id

    gateway_server._mark_notification_delivered(event_id, "email")
    gateway_server._mark_notification_delivered(event_id, "telegram")

    # Read back via the canonical row reader.
    rows = gateway_server._list_undelivered_high_severity_notifications(limit=10)
    matching = [r for r in rows if str(r.get("id") or "") == event_id]
    assert matching, f"row {event_id} should still be retrievable; got {rows}"
    metadata = matching[0].get("metadata") or {}
    delivery = metadata.get("delivery") or {}
    assert "email" in delivery, f"email delivery state missing; got {delivery}"
    assert "telegram" in delivery
    assert metadata.get("job_id") == "seed_job", "original metadata must not be wiped"


@pytest.mark.asyncio
async def test_dispatcher_skips_when_telegram_chat_id_missing():
    """Telegram requires a chat_id; if none configured, skip telegram
    delivery silently (still deliver email)."""
    rows = [_row(event_id="ntf_no_tg")]
    delivered: list[tuple[str, str]] = []
    email = _Recorder()
    telegram = _Recorder()

    dispatcher = NotificationDispatcher(
        get_pending_rows=lambda: list(rows),
        mark_delivered=lambda event_id, channel: delivered.append((event_id, channel)),
        send_email=email,
        send_telegram=telegram,
        email_targets=["test@example.com"],
        telegram_chat_id=None,
        now_fn=lambda: time.time(),
    )

    summary = await dispatcher.dispatch_pending_once()

    assert summary["email_sent"] == 1
    assert summary["telegram_sent"] == 0
    assert summary["telegram_skipped_no_chat_id"] == 1
    assert telegram.calls == []
