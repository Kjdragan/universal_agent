"""Cron artifact reminder sweep.

Companion to ``cron_artifact_notifier.py``. The notifier sends the
initial email and seeds reminder state in
``proactive_artifacts.metadata_json.reminder``; this module is a
scheduled sweep that walks pending artifacts and sends the cadence
follow-ups:

  - ``sent_initial``      → sent_same_day_nudge at T+4h
  - ``sent_same_day_nudge`` → sent_day3 at T+72h after finished_at
  - ``sent_day3``         → sent_day7 at T+168h after finished_at
  - ``sent_day7``         → ``stopped`` (no more emails; row still
                            visible in morning briefing until acked)

Once an artifact transitions to status=ACCEPTED (via the ack endpoint),
the reminder loop short-circuits.

Active-window gating: reminder emails (NOT the initial one — that
fires immediately) are sent only between 6 AM and 10 PM Houston time.
A reminder due at 3 AM Houston defers to 6 AM the same day.

This module exposes a single async entrypoint
``sweep_pending_artifact_reminders`` intended to be invoked from a
30-min cron that the gateway registers at boot.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
import time
from typing import Any, Optional

from universal_agent.services import proactive_artifacts
from universal_agent.services.cron_artifact_notifier import _build_ack_url, _escape
from universal_agent.services.email_tags import ActionTag, KindTag

logger = logging.getLogger(__name__)


# ── Cadence configuration ──────────────────────────────────────────────

SAME_DAY_NUDGE_DELAY_S = 4 * 3600
DAY3_DELAY_S = 72 * 3600
DAY7_DELAY_S = 168 * 3600

# State transitions; each value is the next state name + offset (from
# ``finished_at_epoch``) to schedule.
_STATE_TRANSITIONS: dict[str, tuple[str, float]] = {
    "sent_initial": ("sent_same_day_nudge", SAME_DAY_NUDGE_DELAY_S),
    "sent_same_day_nudge": ("sent_day3", DAY3_DELAY_S),
    "sent_day3": ("sent_day7", DAY7_DELAY_S),
}
_TERMINAL_STATE = "stopped"

# Active window (UTC offsets — defaults to America/Chicago = UTC-5/CDT
# or UTC-6/CST). Computed dynamically per-tick so DST transitions are
# handled correctly.
_HOUSTON_TZ_NAME = "America/Chicago"
_ACTIVE_START_HOUR = 6
_ACTIVE_END_HOUR = 22  # 10 PM


# ── Public API ─────────────────────────────────────────────────────────


async def sweep_pending_artifact_reminders(
    *,
    conn: sqlite3.Connection,
    mail_service: Any,
    recipient: str,
    dashboard_base_url: str = "",
    now_epoch: Optional[float] = None,
) -> dict[str, Any]:
    """Walk pending artifacts and send any reminder that has come due.

    Returns a small report: ``{considered, sent, deferred_window,
    stopped, errors}`` for telemetry.

    The sweep is best-effort: per-artifact failures are logged at debug
    and counted in ``errors`` but never propagate.
    """
    now = float(now_epoch) if now_epoch is not None else time.time()
    considered = 0
    sent = 0
    deferred = 0
    stopped = 0
    errors = 0

    proactive_artifacts.ensure_schema(conn)

    # Status filter: still produced/candidate/surfaced (i.e. not
    # accepted/rejected/archived). ``list_artifacts`` only accepts a
    # single status at a time, so union three queries and dedupe.
    pending: list[dict[str, Any]] = []
    _seen: set[str] = set()
    for _status in (
        proactive_artifacts.ARTIFACT_STATUS_PRODUCED,
        proactive_artifacts.ARTIFACT_STATUS_CANDIDATE,
        proactive_artifacts.ARTIFACT_STATUS_SURFACED,
    ):
        for row in proactive_artifacts.list_artifacts(
            conn, status=_status, limit=500
        ):
            aid = str(row.get("artifact_id") or "").strip()
            if aid and aid not in _seen:
                _seen.add(aid)
                pending.append(row)
    for artifact in pending:
        considered += 1
        try:
            meta = _load_metadata(artifact)
            reminder = meta.get("reminder") or {}
            if not reminder:
                continue  # not a cron-disclosure artifact
            if bool(reminder.get("stopped")):
                continue
            current_state = str(reminder.get("schedule_state") or "").strip()
            transition = _STATE_TRANSITIONS.get(current_state)
            if transition is None:
                # Either already at terminal state or shape is unknown.
                if current_state == _TERMINAL_STATE:
                    continue
                # Unknown state — mark stopped so we don't loop forever.
                reminder["stopped"] = True
                _persist_reminder(conn, artifact, meta, reminder)
                stopped += 1
                continue
            next_state, _ = transition
            next_at = float(reminder.get("next_reminder_at_epoch") or 0.0)
            if next_at <= 0 or now < next_at:
                continue  # not due yet
            if not _within_active_window(now):
                deferred += 1
                continue
            ok = await _send_reminder(
                conn=conn,
                mail_service=mail_service,
                artifact=artifact,
                recipient=recipient,
                dashboard_base_url=dashboard_base_url,
                next_state=next_state,
            )
            if not ok:
                errors += 1
                continue
            # Persist the next transition.
            sent += 1
            new_reminder = _advance_reminder_state(
                reminder, next_state=next_state, now=now, artifact=artifact
            )
            _persist_reminder(conn, artifact, meta, new_reminder)
            if new_reminder.get("stopped"):
                stopped += 1
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug(
                "cron_artifact_reminders: per-artifact failure for %s: %s",
                artifact.get("artifact_id"),
                exc,
            )
            errors += 1

    report = {
        "considered": considered,
        "sent": sent,
        "deferred_window": deferred,
        "stopped": stopped,
        "errors": errors,
    }
    if sent or errors:
        logger.info("cron_artifact_reminders sweep: %s", report)
    return report


# ── Helpers ────────────────────────────────────────────────────────────


def _load_metadata(artifact: dict[str, Any]) -> dict[str, Any]:
    raw = artifact.get("metadata") or artifact.get("metadata_json") or {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _persist_reminder(
    conn: sqlite3.Connection,
    artifact: dict[str, Any],
    metadata: dict[str, Any],
    reminder: dict[str, Any],
) -> None:
    """Write the updated reminder block back into ``metadata_json``.

    ``proactive_artifacts.update_artifact_state`` doesn't accept a
    metadata override, so we issue a targeted UPDATE here. The metadata
    column is the only field we touch — status/delivery_state stay put.
    """
    metadata["reminder"] = reminder
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    if not artifact_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE proactive_artifacts
        SET metadata_json = ?, updated_at = ?
        WHERE artifact_id = ?
        """,
        (json.dumps(metadata, default=str), now, artifact_id),
    )
    conn.commit()


def _advance_reminder_state(
    reminder: dict[str, Any],
    *,
    next_state: str,
    now: float,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Bump the reminder state machine and compute the next due time."""
    finished_at = _finished_at_epoch(reminder, artifact)
    out = dict(reminder)
    out["schedule_state"] = next_state
    out["count"] = int(out.get("count", 0) or 0) + 1
    out["last_sent_at_epoch"] = now
    transition = _STATE_TRANSITIONS.get(next_state)
    if transition is None:
        out["next_reminder_at_epoch"] = 0
        out["stopped"] = True
    else:
        _, offset = transition
        out["next_reminder_at_epoch"] = finished_at + offset
    return out


def _finished_at_epoch(reminder: dict[str, Any], artifact: dict[str, Any]) -> float:
    raw = reminder.get("finished_at_epoch")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    metadata = artifact.get("metadata") or {}
    if isinstance(metadata, dict):
        raw = metadata.get("finished_at_epoch")
        if raw:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    # Fallback: use surfaced_at, then created_at.
    for key in ("surfaced_at", "created_at"):
        ts = artifact.get(key)
        if not ts:
            continue
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
    return time.time()


def _within_active_window(now_epoch: float) -> bool:
    """Return True if the given UTC epoch falls within Houston active hours."""
    try:
        from zoneinfo import ZoneInfo

        houston = datetime.fromtimestamp(now_epoch, tz=timezone.utc).astimezone(
            ZoneInfo(_HOUSTON_TZ_NAME)
        )
        return _ACTIVE_START_HOUR <= houston.hour < _ACTIVE_END_HOUR
    except Exception:  # noqa: BLE001
        # If zoneinfo or tzdata isn't available, default to permissive:
        # treat every hour as active so reminders aren't lost.
        return True


# ── Email composition for reminders ────────────────────────────────────


async def _send_reminder(
    *,
    conn: sqlite3.Connection,
    mail_service: Any,
    artifact: dict[str, Any],
    recipient: str,
    dashboard_base_url: str,
    next_state: str,
) -> bool:
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    if not artifact_id:
        return False
    subject, text_body, html_body = _compose_reminder_email(
        artifact=artifact,
        next_state=next_state,
        dashboard_base_url=dashboard_base_url,
    )
    try:
        result = await mail_service.send_email(
            to=recipient,
            subject=subject,
            text=text_body,
            html=html_body,
            force_send=True,
            require_approval=False,
            action=ActionTag.FYI,
            kind=KindTag.PROACTIVE,
            source=f"cron_artifact_reminders.{next_state}",
            related=[f"artifact_id={artifact_id}", f"reminder_state={next_state}"],
        )
        proactive_artifacts.record_email_delivery(
            conn,
            artifact_id=artifact_id,
            message_id=str((result or {}).get("message_id") or ""),
            thread_id=str((result or {}).get("thread_id") or ""),
            subject=subject,
            recipient=recipient,
            metadata={
                "kind": "reminder",
                "reminder_state": next_state,
                "mail_status": str((result or {}).get("status") or ""),
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cron_artifact_reminders: send_email failed for %s (%s): %s",
            artifact_id,
            next_state,
            exc,
        )
        return False


_REMINDER_HEADLINES = {
    "sent_same_day_nudge": "Still pending: ",
    "sent_day3": "Day-3 reminder: ",
    "sent_day7": "Day-7 reminder (final): ",
}


def _compose_reminder_email(
    *,
    artifact: dict[str, Any],
    next_state: str,
    dashboard_base_url: str,
) -> tuple[str, str, str]:
    title = str(artifact.get("title") or "").strip()
    summary = str(artifact.get("summary") or "").strip()
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    workspace = str(artifact.get("artifact_path") or "").strip()

    headline = _REMINDER_HEADLINES.get(next_state, "Reminder: ")
    subject = f"{headline}{title}"[:200]

    ack_url = _build_ack_url(artifact_id, dashboard_base_url)
    text_lines = [
        f"{headline}{title}",
        "",
        summary,
        "",
        f"Workspace: {workspace}",
    ]
    if ack_url:
        text_lines.append(f"Acknowledge: {ack_url}")
    if dashboard_base_url and artifact_id:
        text_lines.append(
            f"Dashboard: {dashboard_base_url.rstrip('/')}/dashboard/todolist?artifact={artifact_id}"
        )
    if next_state == "sent_day7":
        text_lines.append("")
        text_lines.append(
            "This is the final reminder. The artifact stays in the dashboard "
            "until acknowledged or archived."
        )
    text_body = "\n".join(text_lines)

    ack_block = (
        f'<p><a href="{_escape(ack_url)}" '
        'style="background:#0a7;color:#fff;padding:8px 16px;'
        'text-decoration:none;border-radius:4px;">Acknowledge</a></p>'
        if ack_url
        else ""
    )
    dashboard_block = (
        f'<p>Open in dashboard: '
        f'<a href="{_escape(dashboard_base_url.rstrip("/"))}/dashboard/todolist?artifact={_escape(artifact_id)}">'
        "Task Hub</a></p>"
        if dashboard_base_url and artifact_id
        else ""
    )
    final_block = (
        "<p><em>This is the final reminder. The artifact stays in the dashboard "
        "until acknowledged or archived.</em></p>"
        if next_state == "sent_day7"
        else ""
    )
    html_body = (
        f"<p><strong>{_escape(headline)}{_escape(title)}</strong></p>"
        f"<p>{_escape(summary)}</p>"
        f"<p>Workspace: <code>{_escape(workspace)}</code></p>"
        f"{ack_block}"
        f"{dashboard_block}"
        f"{final_block}"
    )
    return subject, text_body, html_body


__all__ = ["sweep_pending_artifact_reminders"]
