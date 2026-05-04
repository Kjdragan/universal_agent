"""Async dispatcher that drains high-severity notifications to email + Telegram.

Background:

The gateway's `_add_notification` writes records to the `activity_events`
table with `channels_json=["dashboard","email","telegram"]` and
`email_targets_json=["kevinjdragan@gmail.com"]`.  Today only the dashboard
channel has a consumer — the dashboard polls the activity-events API and
renders alerts.  Email and Telegram targets exist on each row but no
out-of-band consumer drains them, which means the user has to actively
look at the dashboard to see operator-actionable alerts (e.g. a cron run
failed, a heartbeat startup error, missing required secrets).

`NotificationDispatcher` closes that gap.  Every interval, it:

  1. Pulls a small window of recent rows from the activity store.
  2. Filters to severity in {"error", "warning"}.
  3. For each undelivered row, sends to each configured channel.
  4. Marks the row's per-channel delivery state (in `metadata.delivery`)
     so a restart does not re-blast historical state.
  5. Applies a per-kind cooldown so a flapping notification kind cannot
     spam the operator faster than the cooldown allows.

Failure isolation: a send failure on one channel does NOT block the
other channels for the same row, and does NOT mark that channel
delivered (so the row stays eligible for retry on the next tick).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger(__name__)


_DELIVERABLE_SEVERITIES = frozenset({"error", "warning"})
_DEFAULT_COOLDOWN_SECONDS = 300.0  # 5 min per-kind cooldown


def _delivery_state_for_channel(record: dict, channel: str) -> Optional[dict]:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    delivery = metadata.get("delivery")
    if not isinstance(delivery, dict):
        return None
    entry = delivery.get(channel)
    if isinstance(entry, dict):
        return entry
    return None


def _row_already_delivered(record: dict, channel: str) -> bool:
    """Return True if the row has been delivered for this channel
    AND the delivery is still current (delivered_at >= updated_at).

    A flapping kind that gets re-upserted with a new updated_at counts
    as new content and would be eligible for re-delivery — except the
    cooldown will then suppress it.  Combined with kind-upsert in
    `_add_notification`, this gives one delivery per cooldown window
    even under heavy churn.
    """
    state = _delivery_state_for_channel(record, channel)
    if not state:
        return False
    delivered_at = str(state.get("delivered_at") or "").strip()
    if not delivered_at:
        return False
    updated_at = str(record.get("updated_at") or "").strip()
    if not updated_at:
        return True
    # ISO timestamps compare lexically.
    return delivered_at >= updated_at


def _channels_list(record: dict) -> list[str]:
    raw = record.get("channels")
    if not isinstance(raw, list):
        return []
    return [str(x).strip().lower() for x in raw if str(x).strip()]


def _format_email_html(record: dict) -> str:
    title = str(record.get("title") or "Alert")
    message = str(record.get("full_message") or record.get("summary") or "")
    severity = str(record.get("severity") or "info").upper()
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    kind = str(record.get("kind") or "")
    rows = []
    for key in ("job_id", "task_name", "component", "system_job", "error"):
        value = metadata.get(key)
        if value:
            rows.append(f"<tr><td><b>{key}</b></td><td>{value}</td></tr>")
    rows_html = "\n".join(rows) if rows else ""
    return (
        f"<html><body style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.5;\">"
        f"<h2 style=\"color:#b00020;\">[{severity}] {title}</h2>"
        f"<p>{message}</p>"
        + (f"<table cellpadding=4 cellspacing=0 border=1>{rows_html}</table>" if rows_html else "")
        + (f"<p style=\"color:#666;font-size:11px;\">kind: {kind}</p>" if kind else "")
        + "</body></html>"
    )


def _format_telegram_text(record: dict) -> str:
    severity = str(record.get("severity") or "info").upper()
    title = str(record.get("title") or "Alert")
    message = str(record.get("full_message") or record.get("summary") or "")
    if len(message) > 800:
        message = message[:797] + "..."
    return f"[{severity}] {title}\n\n{message}"


class NotificationDispatcher:
    def __init__(
        self,
        *,
        get_pending_rows: Callable[[], Iterable[dict]],
        mark_delivered: Callable[[str, str], None],
        send_email: Callable[..., Awaitable[Any]],
        send_telegram: Callable[..., Awaitable[Any]],
        email_targets: list[str],
        telegram_chat_id: Optional[str | int],
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._get_pending_rows = get_pending_rows
        self._mark_delivered = mark_delivered
        self._send_email = send_email
        self._send_telegram = send_telegram
        self._email_targets = list(email_targets or [])
        self._telegram_chat_id = telegram_chat_id
        self._cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._now = now_fn
        # Per-kind+channel last-send timestamps for cooldown enforcement.
        self._last_send_at: dict[tuple[str, str], float] = {}

    def _within_cooldown(self, kind: str, channel: str) -> bool:
        last = self._last_send_at.get((kind, channel))
        if last is None:
            return False
        return (self._now() - last) < self._cooldown_seconds

    def _record_send(self, kind: str, channel: str) -> None:
        self._last_send_at[(kind, channel)] = self._now()

    async def _deliver_email_for_row(self, record: dict, summary: dict) -> None:
        if "email" not in _channels_list(record):
            return
        if _row_already_delivered(record, "email"):
            return
        kind = str(record.get("kind") or "")
        if self._within_cooldown(kind, "email"):
            summary["email_cooldown_skipped"] += 1
            return
        if not self._email_targets:
            summary["email_skipped_no_target"] += 1
            return

        title = str(record.get("title") or "Alert")
        text = str(record.get("full_message") or record.get("summary") or "")
        html = _format_email_html(record)
        targets = list(record.get("email_targets") or self._email_targets)
        if not targets:
            summary["email_skipped_no_target"] += 1
            return

        delivered_any = False
        for target in targets:
            try:
                await self._send_email(
                    to=str(target),
                    subject=f"[{str(record.get('severity') or 'alert').upper()}] {title}",
                    text=text,
                    html=html,
                )
                delivered_any = True
            except Exception:
                logger.exception(
                    "notification_dispatcher: email send failed for kind=%s target=%s",
                    kind, target,
                )

        if delivered_any:
            self._record_send(kind, "email")
            try:
                self._mark_delivered(str(record.get("id") or ""), "email")
            except Exception:
                logger.exception("notification_dispatcher: mark_delivered(email) failed")
            summary["email_sent"] += 1
        else:
            summary["email_failed"] += 1

    async def _deliver_telegram_for_row(self, record: dict, summary: dict) -> None:
        if "telegram" not in _channels_list(record):
            return
        if _row_already_delivered(record, "telegram"):
            return
        kind = str(record.get("kind") or "")
        if self._within_cooldown(kind, "telegram"):
            summary["telegram_cooldown_skipped"] += 1
            return
        if self._telegram_chat_id is None or str(self._telegram_chat_id).strip() == "":
            summary["telegram_skipped_no_chat_id"] += 1
            return

        text = _format_telegram_text(record)
        try:
            await self._send_telegram(
                chat_id=self._telegram_chat_id,
                text=text,
            )
        except Exception:
            logger.exception(
                "notification_dispatcher: telegram send failed for kind=%s",
                kind,
            )
            summary["telegram_failed"] += 1
            return

        self._record_send(kind, "telegram")
        try:
            self._mark_delivered(str(record.get("id") or ""), "telegram")
        except Exception:
            logger.exception("notification_dispatcher: mark_delivered(telegram) failed")
        summary["telegram_sent"] += 1

    async def dispatch_pending_once(self) -> dict:
        summary = {
            "rows_seen": 0,
            "rows_eligible": 0,
            "email_sent": 0,
            "email_failed": 0,
            "email_cooldown_skipped": 0,
            "email_skipped_no_target": 0,
            "telegram_sent": 0,
            "telegram_failed": 0,
            "telegram_cooldown_skipped": 0,
            "telegram_skipped_no_chat_id": 0,
        }

        try:
            rows = list(self._get_pending_rows() or [])
        except Exception:
            logger.exception("notification_dispatcher: get_pending_rows failed")
            return summary

        for record in rows:
            summary["rows_seen"] += 1
            severity = str(record.get("severity") or "info").strip().lower()
            if severity not in _DELIVERABLE_SEVERITIES:
                continue
            summary["rows_eligible"] += 1
            await self._deliver_email_for_row(record, summary)
            await self._deliver_telegram_for_row(record, summary)

        return summary

    async def run_loop(self, interval_seconds: float, *, stop_event: Optional[asyncio.Event] = None) -> None:
        interval = max(5.0, float(interval_seconds))
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                summary = await self.dispatch_pending_once()
                if summary["email_sent"] or summary["telegram_sent"]:
                    logger.info(
                        "notification_dispatcher tick: email=%d telegram=%d (eligible=%d)",
                        summary["email_sent"],
                        summary["telegram_sent"],
                        summary["rows_eligible"],
                    )
            except Exception:
                logger.exception("notification_dispatcher: tick failed")
            try:
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval)
                        return
                    except asyncio.TimeoutError:
                        continue
                else:
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
