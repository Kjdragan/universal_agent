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
from html import escape as _html_escape
import logging
import time
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger(__name__)


_DELIVERABLE_SEVERITIES = frozenset({"error", "warning"})
_DEFAULT_COOLDOWN_SECONDS = 300.0  # 5 min per-(kind, scope) cooldown
_DEFAULT_ROLLUP_WINDOW_SECONDS = 180.0  # per-kind email rollup window
_ROLLUP_SAMPLE_CAP = 20  # max collapsed events listed in a rollup email


def _scope_key_for_record(record: dict) -> str:
    """Extract the per-notification dedup scope from a record.

    The cooldown key is ``(kind, scope, channel)`` rather than just
    ``(kind, channel)`` — that way two genuinely-different
    ``execution_missing_lifecycle_mutation`` events for different
    task_ids (or different sessions) can alert independently within
    the cooldown window, while a single misbehaving task that keeps
    firing the same kind gets coalesced.

    Resolution order:
      1. ``metadata.task_id`` (Task Hub item id)
      2. ``entity_ref.task_id`` or ``entity_ref.id``
      3. ``metadata.job_id`` (cron job id)
      4. ``metadata.run_id``
      5. ``session_id``
      6. ``""`` — falls back to legacy per-kind behaviour
    """
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    entity_ref = record.get("entity_ref") if isinstance(record.get("entity_ref"), dict) else {}
    for key, source in (
        ("task_id", metadata),
        ("task_id", entity_ref),
        ("id", entity_ref),
        ("job_id", metadata),
        ("run_id", metadata),
    ):
        value = str(source.get(key) or "").strip()
        if value:
            return value
    session_id = str(record.get("session_id") or "").strip()
    return session_id


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
    """Return True if the row has been delivered for this channel and delivery is current.

    "Current" means ``delivered_at >= updated_at``. A flapping kind that gets
    re-upserted with a new ``updated_at`` counts as new content and is eligible
    for re-delivery — except the cooldown will then suppress it. Combined with
    kind-upsert in ``_add_notification``, this gives one delivery per cooldown
    window even under heavy churn.
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


# Metadata keys surfaced in the diagnostic-context table, in render order.
# ``task_id``/``run_id``/``workspace_dir``/``tool_calls``/``session_id`` make a
# todo-execution failure (e.g. ``execution_missing_lifecycle_mutation``)
# debuggable straight from the inbox; the trailing keys cover cron/infra alerts.
_EMAIL_CONTEXT_KEYS = (
    "task_id",
    "invalid_task_ids",
    "session_id",
    "run_id",
    "workspace_dir",
    "tool_calls",
    "deploy_restart_casualty",
    "job_id",
    "task_name",
    "component",
    "system_job",
    "error",
)


def _format_email_html(record: dict) -> str:
    title = str(record.get("title") or "Alert")
    message = str(record.get("full_message") or record.get("summary") or "")
    severity = str(record.get("severity") or "info").upper()
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    kind = str(record.get("kind") or "")
    # ``session_id`` lives on the record, not in metadata — fold it in so the
    # context table can show which session produced the alert.
    context = dict(metadata)
    record_session_id = str(record.get("session_id") or "").strip()
    if record_session_id and not str(context.get("session_id") or "").strip():
        context["session_id"] = record_session_id
    rows = []
    for key in _EMAIL_CONTEXT_KEYS:
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        rows.append(
            f"<tr><td><b>{_html_escape(str(key))}</b></td>"
            f"<td>{_html_escape(str(value))}</td></tr>"
        )
    rows_html = "\n".join(rows) if rows else ""
    # Run-log tail: the actual error transcript (e.g. an upstream ZAI 429/FUP
    # line). Escaped and capped, rendered in a <pre> so the operator can read
    # the real failure without opening the VPS.
    log_tail = str(metadata.get("log_tail") or metadata.get("transcript_tail") or "").strip()
    log_html = ""
    if log_tail:
        if len(log_tail) > 3000:
            log_tail = "…(truncated)…\n" + log_tail[-3000:]
        log_html = (
            "<p style=\"margin:12px 0 4px;\"><b>Run log tail</b></p>"
            "<pre style=\"background:#f6f8fa;border:1px solid #ddd;padding:8px;"
            "white-space:pre-wrap;word-break:break-word;font-size:12px;"
            "overflow-x:auto;\">"
            f"{_html_escape(log_tail)}</pre>"
        )
    return (
        f"<html><body style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.5;\">"
        f"<h2 style=\"color:#b00020;\">[{severity}] {title}</h2>"
        f"<p>{message}</p>"
        + (f"<table cellpadding=4 cellspacing=0 border=1>{rows_html}</table>" if rows_html else "")
        + log_html
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


def _format_rollup_email(kind: str, count: int, samples: list[str]) -> tuple[str, str, str]:
    """Compose the single rollup email for ``count`` collapsed same-kind alerts.

    The first alert of the kind was already emailed individually; this rollup
    covers the additional ones that fired within the window. It lists up to
    ``_ROLLUP_SAMPLE_CAP`` samples and notes any overflow so no event is
    silently dropped.
    """
    subject = f"[ALERT ROLLUP] {kind} × {count} additional alert(s)"
    lines = [
        f"{count} additional '{kind}' alert(s) fired within the rollup window "
        "(the first one was emailed separately).",
        "",
        "Collapsed events:",
    ]
    lines.extend(f"  - {s}" for s in samples)
    if count > len(samples):
        lines.append(f"  - ... and {count - len(samples)} more")
    lines.append("")
    lines.append("See the dashboard for full details on each.")
    text = "\n".join(lines)

    items_html = "".join(f"<li>{_html_escape(s)}</li>" for s in samples)
    if count > len(samples):
        items_html += f"<li>... and {count - len(samples)} more</li>"
    html = (
        "<html><body style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height:1.5;\">"
        f"<h2 style=\"color:#b00020;\">[ALERT ROLLUP] {_html_escape(kind)} &times; {count}</h2>"
        f"<p>{count} additional '<code>{_html_escape(kind)}</code>' alert(s) fired within the rollup "
        "window (the first one was emailed separately).</p>"
        f"<ul>{items_html}</ul>"
        "<p style=\"color:#666;font-size:11px;\">See the dashboard for full details on each.</p>"
        "</body></html>"
    )
    return subject, text, html


class NotificationDispatcher:
    """Dispatches pending notifications to email and Telegram channels."""

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
        rollup_enabled: bool = True,
        rollup_window_seconds: float = _DEFAULT_ROLLUP_WINDOW_SECONDS,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        """Initialize the dispatcher with delivery callables and targeting config."""
        self._get_pending_rows = get_pending_rows
        self._mark_delivered = mark_delivered
        self._send_email = send_email
        self._send_telegram = send_telegram
        self._email_targets = list(email_targets or [])
        self._telegram_chat_id = telegram_chat_id
        self._cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._rollup_enabled = bool(rollup_enabled)
        self._rollup_window_seconds = max(1.0, float(rollup_window_seconds))
        self._now = now_fn
        # Per-(kind, scope, channel) last-send timestamps for cooldown
        # enforcement. ``scope`` is a task_id / job_id / session_id
        # extracted by ``_scope_key_for_record`` so different tasks of the
        # same kind alert independently within the cooldown window.
        self._last_send_at: dict[tuple[str, str, str], float] = {}
        # Per-kind email rollup windows. The first alert of a kind sends
        # immediately and opens a window here; same-kind alerts arriving
        # while the window is open are buffered into ``count``/``samples``
        # and emitted as one rollup email when the window expires. This
        # caps incident fan-out (many distinct scopes of one kind failing
        # at once) that the per-(kind, scope) cooldown cannot coalesce.
        self._rollup_open: dict[str, dict[str, Any]] = {}

    def _within_cooldown(self, kind: str, scope: str, channel: str) -> bool:
        last = self._last_send_at.get((kind, scope, channel))
        if last is None:
            return False
        return (self._now() - last) < self._cooldown_seconds

    def _record_send(self, kind: str, scope: str, channel: str) -> None:
        self._last_send_at[(kind, scope, channel)] = self._now()

    def _rollup_window_open(self, kind: str) -> bool:
        window = self._rollup_open.get(kind)
        if window is None:
            return False
        return (self._now() - float(window["opened_at"])) < self._rollup_window_seconds

    def _rollup_start(self, kind: str) -> None:
        self._rollup_open[kind] = {"opened_at": self._now(), "count": 0, "samples": []}

    def _rollup_buffer(self, kind: str, record: dict) -> None:
        window = self._rollup_open.get(kind)
        if window is None:
            return
        window["count"] = int(window["count"]) + 1
        samples = window["samples"]
        if len(samples) < _ROLLUP_SAMPLE_CAP:
            title = str(record.get("title") or "Alert")
            scope = _scope_key_for_record(record) or "-"
            samples.append(f"{title} (scope: {scope})")

    async def _flush_expired_rollups(self, summary: dict) -> None:
        """Emit one rollup email per kind whose window has expired with buffered events."""
        if not self._rollup_enabled or not self._rollup_open:
            return
        now = self._now()
        expired = [
            kind
            for kind, window in self._rollup_open.items()
            if (now - float(window["opened_at"])) >= self._rollup_window_seconds
        ]
        for kind in expired:
            window = self._rollup_open.pop(kind, None)
            if not window or int(window.get("count") or 0) <= 0:
                continue  # isolated alert — first send already covered it
            targets = list(self._email_targets)
            if not targets:
                summary["email_skipped_no_target"] += 1
                continue
            subject, text, html = _format_rollup_email(
                kind, int(window["count"]), list(window["samples"])
            )
            delivered_any = False
            for target in targets:
                try:
                    await self._send_email(to=str(target), subject=subject, text=text, html=html)
                    delivered_any = True
                except Exception:
                    logger.exception(
                        "notification_dispatcher: rollup email send failed for kind=%s", kind
                    )
            if delivered_any:
                summary["rollup_emails_sent"] += 1
            else:
                summary["email_failed"] += 1

    async def _deliver_email_for_row(self, record: dict, summary: dict) -> None:
        if "email" not in _channels_list(record):
            return
        if _row_already_delivered(record, "email"):
            return
        kind = str(record.get("kind") or "")
        scope = _scope_key_for_record(record)
        # Per-kind rollup: while a kind's window is open, buffer same-kind
        # alerts (any scope) into a single rollup instead of emailing each.
        # The row is marked delivered so it cannot re-surface and double-send.
        if self._rollup_enabled and self._rollup_window_open(kind):
            self._rollup_buffer(kind, record)
            summary["email_rolled_up"] += 1
            try:
                self._mark_delivered(str(record.get("id") or ""), "email")
            except Exception:
                logger.exception("notification_dispatcher: mark_delivered(email rollup) failed")
            return
        if self._within_cooldown(kind, scope, "email"):
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
                    "notification_dispatcher: email send failed for kind=%s scope=%s target=%s",
                    kind, scope, target,
                )

        if delivered_any:
            self._record_send(kind, scope, "email")
            try:
                self._mark_delivered(str(record.get("id") or ""), "email")
            except Exception:
                logger.exception("notification_dispatcher: mark_delivered(email) failed")
            summary["email_sent"] += 1
            # Open a rollup window so further same-kind alerts within it
            # collapse into one rollup instead of emailing individually.
            if self._rollup_enabled:
                self._rollup_start(kind)
        else:
            summary["email_failed"] += 1

    async def _deliver_telegram_for_row(self, record: dict, summary: dict) -> None:
        if "telegram" not in _channels_list(record):
            return
        if _row_already_delivered(record, "telegram"):
            return
        kind = str(record.get("kind") or "")
        scope = _scope_key_for_record(record)
        if self._within_cooldown(kind, scope, "telegram"):
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
                "notification_dispatcher: telegram send failed for kind=%s scope=%s",
                kind, scope,
            )
            summary["telegram_failed"] += 1
            return

        self._record_send(kind, scope, "telegram")
        try:
            self._mark_delivered(str(record.get("id") or ""), "telegram")
        except Exception:
            logger.exception("notification_dispatcher: mark_delivered(telegram) failed")
        summary["telegram_sent"] += 1

    async def dispatch_pending_once(self) -> dict:
        """Dispatch all pending notifications once and return a delivery summary."""
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
            "email_rolled_up": 0,
            "rollup_emails_sent": 0,
        }

        try:
            rows = list(self._get_pending_rows() or [])
        except Exception:
            logger.exception("notification_dispatcher: get_pending_rows failed")
            # Still flush any rollup windows that have come due.
            await self._flush_expired_rollups(summary)
            return summary

        for record in rows:
            summary["rows_seen"] += 1
            severity = str(record.get("severity") or "info").strip().lower()
            if severity not in _DELIVERABLE_SEVERITIES:
                continue
            summary["rows_eligible"] += 1
            await self._deliver_email_for_row(record, summary)
            await self._deliver_telegram_for_row(record, summary)

        # Emit any rollup emails whose window expired during/before this tick.
        await self._flush_expired_rollups(summary)

        return summary

    async def run_loop(self, interval_seconds: float, *, stop_event: Optional[asyncio.Event] = None) -> None:
        """Run dispatch_pending_once on a repeating interval until stop_event is set."""
        interval = max(5.0, float(interval_seconds))
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                summary = await self.dispatch_pending_once()
                if summary["email_sent"] or summary["telegram_sent"] or summary["rollup_emails_sent"]:
                    logger.info(
                        "notification_dispatcher tick: email=%d telegram=%d rollup=%d (rolled_up=%d eligible=%d)",
                        summary["email_sent"],
                        summary["telegram_sent"],
                        summary["rollup_emails_sent"],
                        summary["email_rolled_up"],
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
