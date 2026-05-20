"""Delivery-reminder signals for the Daily YouTube Digest cron.

Background: AgentMail returning 200 OK ≠ "the operator saw the digest".
On 2026-05-19 a perfectly-delivered 228 KB digest looked like a 13-hour
silent failure because nothing surfaced "we sent it" to the operator's
second channel.  This service fixes that by emitting two short-lived
delivery reminders (Telegram message + dashboard tile) when the digest
email is accepted by AgentMail.

Hard constraints (encoded as design invariants — DO NOT relax):

1. Never touch the original Gmail message.  The Telegram bot only ever
   calls ``deleteMessage`` against its OWN messages (``chat_id`` +
   ``message_id``); it has no Gmail / AgentMail credentials and no code
   path that reaches into either.  See :func:`_schedule_dismissal`.

2. No new email channel.  Telegram + dashboard only.  This module never
   imports AgentMail / SMTP / IMAP.

3. Reminders self-expire after ``UA_DIGEST_REMINDER_TTL_MINUTES`` (default
   90).  After the TTL elapses:
     - The dashboard notification is filtered out by the gateway query
       (``expires_at`` clause in ``_query_activity_events``).
     - The Telegram message is deleted by the gateway's periodic
       reminder-sweep loop (see ``gateway_server.py``: the
       ``_digest_reminder_dismissal_sweep`` task).

The TTL value is read from ``UA_DIGEST_REMINDER_TTL_MINUTES`` at call
time, so operators can flip it via env without a code change.

Persistence design:

  - Telegram reminders are recorded in the activity DB
    (``digest_telegram_reminders`` table) so the dismissal sweep
    survives gateway restarts (the digest cron may run on a different
    process from the gateway worker that ultimately fires the
    deleteMessage).

  - Dashboard notifications use the existing ``activity_events`` table
    via :func:`emit_intelligence_event`, with a new ``expires_at``
    column.

This module is intentionally importable from a cron subprocess that has
NO gateway in-process — it speaks SQLite + HTTPS (Telegram API) only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3
from typing import Optional
from zoneinfo import ZoneInfo

from universal_agent.services.intelligence_emitter import (
    SEVERITY_SUCCESS,
    emit_intelligence_event,
)
from universal_agent.services.telegram_send import (
    telegram_send_with_response_sync,
)

logger = logging.getLogger(__name__)

DEFAULT_TTL_MINUTES = 90
HOUSTON_TZ = ZoneInfo("America/Chicago")
KIND_DAILY_DIGEST_DELIVERED = "daily_digest_delivered"


@dataclass(frozen=True)
class DigestDeliveryReminderResult:
    """Outcome of a reminder fire.  All fields are optional because the
    reminder is best-effort — a single channel failing must not be
    interpreted as digest delivery failing."""
    telegram_ok: bool
    telegram_message_id: Optional[int]
    telegram_error: Optional[str]
    dashboard_event_id: Optional[str]
    expires_at_iso: str


def _resolve_ttl_minutes(override: Optional[int]) -> int:
    if override is not None and override > 0:
        return int(override)
    raw = (os.getenv("UA_DIGEST_REMINDER_TTL_MINUTES") or "").strip()
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            logger.warning(
                "UA_DIGEST_REMINDER_TTL_MINUTES=%r is not an int; using default %d",
                raw,
                DEFAULT_TTL_MINUTES,
            )
    return DEFAULT_TTL_MINUTES


def _resolve_chat_id(override: Optional[str | int]) -> Optional[str]:
    """Resolve the operator's Telegram chat_id.

    Priority: explicit override > UA_OPERATOR_TELEGRAM_CHAT_ID > TELEGRAM_CHAT_ID.
    Returns None when nothing is configured — the caller treats that as
    "Telegram channel disabled" and proceeds with the dashboard tile alone.
    """
    if override is not None:
        text = str(override).strip()
        if text:
            return text
    for env_name in ("UA_OPERATOR_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID"):
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            return raw
    return None


def _format_houston(dt_utc: datetime) -> str:
    """e.g. '6:14 AM Tue May 19 CDT' — Houston-local, never UTC."""
    local = dt_utc.astimezone(HOUSTON_TZ)
    # %-I drops the leading zero on the hour (POSIX); on systems where
    # this fails we trim manually after %I.
    try:
        return local.strftime("%-I:%M %p %a %b %-d %Z")
    except ValueError:
        return local.strftime("%I:%M %p %a %b %d %Z").lstrip("0")


def _compose_telegram_text(
    *,
    subject: str,
    recipient: str,
    sent_at_houston: str,
    gmail_thread_url: Optional[str],
) -> str:
    lines = [
        "📬 Daily YouTube Digest delivered",
        f"Subject: {subject}",
        f"To: {recipient}",
        f"Sent: {sent_at_houston}",
    ]
    if gmail_thread_url:
        lines.append(f"Gmail: {gmail_thread_url}")
    return "\n".join(lines)


def _activity_db_path() -> str:
    """Path to the activity / notification SQLite DB used by the gateway."""
    from universal_agent.durable.db import get_activity_db_path
    return get_activity_db_path()


def _ensure_reminder_schedule_table(conn: sqlite3.Connection) -> None:
    """Schedule table consumed by the gateway dismissal sweep loop.

    Rows are written here when the digest script fires a Telegram
    reminder; the gateway's periodic sweep picks up rows whose
    ``dismiss_at`` has elapsed, calls Telegram ``deleteMessage`` for
    ``(chat_id, message_id)``, and marks the row deleted.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS digest_telegram_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            dismiss_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            dismissed_at TEXT,
            dismiss_status TEXT,
            UNIQUE(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_digest_telegram_reminders_dismiss_at
            ON digest_telegram_reminders(dismiss_at)
            WHERE dismissed_at IS NULL;
        """
    )


def _schedule_dismissal(
    *,
    chat_id: str,
    message_id: int,
    dismiss_at_iso: str,
    db_path: Optional[str] = None,
) -> bool:
    """Record a pending Telegram ``deleteMessage`` for the gateway sweep.

    HARD CONSTRAINT: ``chat_id`` is the operator's Telegram channel and
    ``message_id`` is the Telegram message we just sent.  This row is
    consumed by ``_digest_reminder_dismissal_sweep`` in
    ``gateway_server.py`` which calls ``deleteMessage`` against the
    Telegram API only.  No Gmail/AgentMail surface is touched.
    """
    try:
        path = db_path or _activity_db_path()
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            _ensure_reminder_schedule_table(conn)
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO digest_telegram_reminders
                    (chat_id, message_id, dismiss_at, created_at,
                     dismissed_at, dismiss_status)
                VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (str(chat_id), int(message_id), dismiss_at_iso, now_iso),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "digest_delivery_reminder: failed scheduling Telegram dismissal "
            "chat_id=%s message_id=%s: %s",
            chat_id, message_id, exc,
        )
        return False


def send_digest_delivery_reminder(
    *,
    subject: str,
    recipient: str,
    sent_at_utc: Optional[datetime] = None,
    gmail_thread_url: Optional[str] = None,
    ttl_minutes: Optional[int] = None,
    telegram_chat_id: Optional[str | int] = None,
    bot_token: Optional[str] = None,
    db_path: Optional[str] = None,
) -> DigestDeliveryReminderResult:
    """Fire Telegram ping + dashboard tile for a successfully-delivered digest.

    Both channels are best-effort and never raise — a failure in either
    is logged but does not affect the digest cron's success state.

    Args:
        subject: The email subject line (shown verbatim).
        recipient: The email address the digest was sent to.
        sent_at_utc: Send timestamp.  Defaults to ``now()``.
        gmail_thread_url: Optional — the operator's Gmail thread URL for
            quick navigation.  Stored as metadata + shown in the
            Telegram message body when present.
        ttl_minutes: Override the default 90-min TTL.  Falls back to
            ``UA_DIGEST_REMINDER_TTL_MINUTES`` env var if not passed.
        telegram_chat_id: Override the operator's chat_id.  Falls back
            to ``UA_OPERATOR_TELEGRAM_CHAT_ID`` then ``TELEGRAM_CHAT_ID``.
        bot_token: Telegram bot token override (for tests).  Falls back
            to ``TELEGRAM_BOT_TOKEN`` env.
        db_path: Activity DB path override (for tests).
    """
    sent_at = sent_at_utc or datetime.now(timezone.utc)
    ttl = _resolve_ttl_minutes(ttl_minutes)
    expires_at = sent_at + timedelta(minutes=ttl)
    expires_at_iso = expires_at.astimezone(timezone.utc).isoformat()
    sent_at_houston = _format_houston(sent_at)

    telegram_ok = False
    telegram_message_id: Optional[int] = None
    telegram_error: Optional[str] = None
    chat_id = _resolve_chat_id(telegram_chat_id)

    if chat_id is None:
        telegram_error = "telegram_chat_id_not_configured"
        logger.info(
            "digest_delivery_reminder: Telegram chat_id not configured "
            "(set UA_OPERATOR_TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID); "
            "skipping Telegram ping (dashboard tile will still fire)."
        )
    else:
        text = _compose_telegram_text(
            subject=subject,
            recipient=recipient,
            sent_at_houston=sent_at_houston,
            gmail_thread_url=gmail_thread_url,
        )
        try:
            ok, payload, err = telegram_send_with_response_sync(
                chat_id, text,
                bot_token=bot_token,
                disable_preview=True,
            )
            telegram_ok = bool(ok)
            telegram_error = None if ok else (err or "unknown_error")
            if ok and isinstance(payload, dict):
                result = payload.get("result") if isinstance(payload, dict) else None
                if isinstance(result, dict):
                    raw_mid = result.get("message_id")
                    if isinstance(raw_mid, int):
                        telegram_message_id = raw_mid
                    else:
                        try:
                            telegram_message_id = int(raw_mid)  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            telegram_message_id = None
        except Exception as exc:
            telegram_ok = False
            telegram_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "digest_delivery_reminder: Telegram send raised: %s", exc,
            )

        if telegram_ok and telegram_message_id is not None:
            _schedule_dismissal(
                chat_id=str(chat_id),
                message_id=telegram_message_id,
                dismiss_at_iso=expires_at_iso,
                db_path=db_path,
            )

    # Dashboard tile — fires regardless of Telegram outcome.
    title = f"Daily YouTube Digest: {sent_at.astimezone(HOUSTON_TZ).strftime('%A')}"
    summary = f"Delivered to {recipient} at {sent_at_houston}."
    full_message_lines = [
        f"Subject: {subject}",
        f"Recipient: {recipient}",
        f"Sent: {sent_at_houston}",
    ]
    if gmail_thread_url:
        full_message_lines.append(f"Gmail thread: {gmail_thread_url}")
    full_message_lines.append(
        f"This tile auto-clears in {ttl} minutes "
        f"(or click X to dismiss now)."
    )
    full_message = "\n".join(full_message_lines)

    metadata: dict = {
        "subject": subject,
        "recipient": recipient,
        "sent_at_utc": sent_at.astimezone(timezone.utc).isoformat(),
        "sent_at_houston": sent_at_houston,
        "expires_at": expires_at_iso,
        "ttl_minutes": ttl,
    }
    if gmail_thread_url:
        metadata["gmail_thread_url"] = gmail_thread_url
    if telegram_ok and telegram_message_id is not None and chat_id:
        metadata["telegram_chat_id"] = str(chat_id)
        metadata["telegram_message_id"] = telegram_message_id

    dashboard_event_id = emit_intelligence_event(
        source_domain="youtube_daily_digest",
        kind=KIND_DAILY_DIGEST_DELIVERED,
        title=title,
        summary=summary,
        full_message=full_message,
        severity=SEVERITY_SUCCESS,
        metadata=metadata,
        expires_at=expires_at_iso,
        db_path=db_path,
    )

    return DigestDeliveryReminderResult(
        telegram_ok=telegram_ok,
        telegram_message_id=telegram_message_id,
        telegram_error=telegram_error,
        dashboard_event_id=dashboard_event_id,
        expires_at_iso=expires_at_iso,
    )
