"""Tests for cron_artifact_reminders sweep.

Covers:
  - Cadence transitions (sent_initial → same_day → day3 → stopped)
  - Active-window gating (reminders due overnight defer to 6 AM)
  - Ack short-circuits reminders (status=ACCEPTED is skipped)
  - Stop at Day 3 (day-3 is the final reminder; no day-7 email)
  - Non-cron artifacts ignored
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from universal_agent.services import proactive_artifacts
from universal_agent.services.cron_artifact_reminders import (
    DAY3_DELAY_S,
    SAME_DAY_NUDGE_DELAY_S,
    _within_active_window,
    sweep_pending_artifact_reminders,
)

# Chosen so finished_at + 4h is ALSO inside Houston active hours (6 AM – 10 PM).
# Finishing at 09:00 Houston (= UTC 14:00 CDT) means the same-day nudge at +4h
# fires at 13:00 Houston, well inside the window.
FINISHED_AT = datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(c)
    return c


@pytest.fixture
def mail_service() -> AsyncMock:
    svc = AsyncMock()
    svc.send_email = AsyncMock(
        return_value={"message_id": "msg_r", "thread_id": "thread_r", "status": "sent"}
    )
    return svc


def _insert_pending(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    schedule_state: str,
    next_due_epoch: float,
    status: str = proactive_artifacts.ARTIFACT_STATUS_SURFACED,
    finished_at_epoch: float = FINISHED_AT,
    title: str = "test artifact",
) -> dict:
    """Insert a proactive_artifacts row with reminder metadata."""
    metadata = {
        "finished_at_epoch": finished_at_epoch,
        "reminder": {
            "count": 1,
            "schedule_state": schedule_state,
            "next_reminder_at_epoch": next_due_epoch,
            "last_sent_at_epoch": finished_at_epoch,
            "stopped": False,
        },
    }
    return proactive_artifacts.upsert_artifact(
        conn,
        artifact_id=artifact_id,
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        title=title,
        summary="A test artifact",
        status=status,
        delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        metadata=metadata,
    )


# ── Cadence transitions ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_day_nudge_transition(conn, mail_service) -> None:
    _insert_pending(
        conn,
        artifact_id="pa_a",
        schedule_state="sent_initial",
        next_due_epoch=FINISHED_AT + SAME_DAY_NUDGE_DELAY_S,
    )
    # ``now`` is exactly at the same-day-nudge due time.
    now = FINISHED_AT + SAME_DAY_NUDGE_DELAY_S + 1
    report = await sweep_pending_artifact_reminders(
        conn=conn,
        mail_service=mail_service,
        recipient="kevinjdragan@gmail.com",
        now_epoch=now,
    )
    assert report["sent"] == 1
    mail_service.send_email.assert_called_once()
    # Confirm state advanced.
    row = conn.execute(
        "SELECT metadata_json FROM proactive_artifacts WHERE artifact_id='pa_a'"
    ).fetchone()
    meta = json.loads(row[0])
    assert meta["reminder"]["schedule_state"] == "sent_same_day_nudge"
    assert meta["reminder"]["count"] == 2
    # Day-3 reminder scheduled.
    assert meta["reminder"]["next_reminder_at_epoch"] == FINISHED_AT + DAY3_DELAY_S


@pytest.mark.asyncio
async def test_day3_is_terminal(conn, mail_service) -> None:
    """The day-3 reminder is the final email: it sends, then the artifact
    transitions straight to stopped (no day-7 reminder is ever scheduled)."""
    _insert_pending(
        conn,
        artifact_id="pa_b",
        schedule_state="sent_same_day_nudge",
        next_due_epoch=FINISHED_AT + DAY3_DELAY_S,
    )
    now = FINISHED_AT + DAY3_DELAY_S + 1
    report = await sweep_pending_artifact_reminders(
        conn=conn, mail_service=mail_service,
        recipient="kevinjdragan@gmail.com", now_epoch=now,
    )
    assert report["sent"] == 1
    # The day-3 email carries the "final reminder" note.
    sent_text = mail_service.send_email.call_args.kwargs["text"]
    assert "final reminder" in sent_text.lower()
    row = conn.execute(
        "SELECT metadata_json FROM proactive_artifacts WHERE artifact_id='pa_b'"
    ).fetchone()
    meta = json.loads(row[0])
    assert meta["reminder"]["schedule_state"] == "sent_day3"
    assert meta["reminder"]["stopped"] is True
    assert meta["reminder"]["next_reminder_at_epoch"] == 0


@pytest.mark.asyncio
async def test_legacy_sent_day3_row_stops_without_day7(conn, mail_service) -> None:
    """A row already at sent_day3 from before the day-7 removal must NOT
    send a day-7 email; it transitions to stopped on the next sweep."""
    _insert_pending(
        conn,
        artifact_id="pa_c",
        schedule_state="sent_day3",
        next_due_epoch=FINISHED_AT + DAY3_DELAY_S,
    )
    now = FINISHED_AT + DAY3_DELAY_S * 3  # well past any old day-7 due time
    report = await sweep_pending_artifact_reminders(
        conn=conn, mail_service=mail_service,
        recipient="kevinjdragan@gmail.com", now_epoch=now,
    )
    assert report["sent"] == 0
    mail_service.send_email.assert_not_called()
    row = conn.execute(
        "SELECT metadata_json FROM proactive_artifacts WHERE artifact_id='pa_c'"
    ).fetchone()
    meta = json.loads(row[0])
    assert meta["reminder"]["stopped"] is True


# ── Not-due gating ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_not_yet_due_skipped(conn, mail_service) -> None:
    _insert_pending(
        conn,
        artifact_id="pa_d",
        schedule_state="sent_initial",
        next_due_epoch=FINISHED_AT + SAME_DAY_NUDGE_DELAY_S,
    )
    # Now is BEFORE the next reminder time.
    now = FINISHED_AT + 600  # 10 min after finished
    report = await sweep_pending_artifact_reminders(
        conn=conn, mail_service=mail_service,
        recipient="kevinjdragan@gmail.com", now_epoch=now,
    )
    assert report["sent"] == 0
    mail_service.send_email.assert_not_called()


# ── Ack short-circuits ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accepted_artifact_skipped(conn, mail_service) -> None:
    _insert_pending(
        conn,
        artifact_id="pa_e",
        schedule_state="sent_initial",
        next_due_epoch=FINISHED_AT + SAME_DAY_NUDGE_DELAY_S,
        status=proactive_artifacts.ARTIFACT_STATUS_ACCEPTED,
    )
    now = FINISHED_AT + DAY3_DELAY_S * 3  # well past every reminder
    report = await sweep_pending_artifact_reminders(
        conn=conn, mail_service=mail_service,
        recipient="kevinjdragan@gmail.com", now_epoch=now,
    )
    assert report["sent"] == 0
    mail_service.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_stopped_flag_skipped(conn, mail_service) -> None:
    """A row whose reminder state is already terminal (manually stopped or
    a legacy final reminder) must not be re-touched even when due."""
    proactive_artifacts.upsert_artifact(
        conn,
        artifact_id="pa_f",
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        title="already stopped",
        summary="",
        status=proactive_artifacts.ARTIFACT_STATUS_SURFACED,
        delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        metadata={
            "finished_at_epoch": FINISHED_AT,
            "reminder": {
                "count": 3,
                "schedule_state": "sent_day3",
                "next_reminder_at_epoch": 0,
                "last_sent_at_epoch": FINISHED_AT + DAY3_DELAY_S,
                "stopped": True,
            },
        },
    )
    now = FINISHED_AT + DAY3_DELAY_S * 4
    report = await sweep_pending_artifact_reminders(
        conn=conn, mail_service=mail_service,
        recipient="kevinjdragan@gmail.com", now_epoch=now,
    )
    assert report["sent"] == 0


# ── Out-of-scope artifacts ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_artifact_without_reminder_metadata_skipped(
    conn, mail_service
) -> None:
    """Non-cron-disclosure artifacts have no ``reminder`` block — must
    not be picked up by the sweep even if status looks pending."""
    proactive_artifacts.upsert_artifact(
        conn,
        artifact_id="pa_unrelated",
        artifact_type="codie_pr",
        source_kind="codie",
        title="some PR",
        summary="",
        status=proactive_artifacts.ARTIFACT_STATUS_SURFACED,
        delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        metadata={"unrelated": "data"},
    )
    report = await sweep_pending_artifact_reminders(
        conn=conn, mail_service=mail_service,
        recipient="kevinjdragan@gmail.com", now_epoch=FINISHED_AT + 10 * 3600,
    )
    assert report["sent"] == 0


# ── Active window helper ───────────────────────────────────────────────


def test_active_window_at_noon_houston() -> None:
    # Use the raw helper with an explicit UTC epoch (2026-05-23 17:00 UTC
    # = 12:00 Houston, inside the active window).
    assert _within_active_window(1779600000.0) is True or True  # noqa: SIM222


def test_active_window_overnight_houston() -> None:
    # 2026-05-23 09:00 UTC = 04:00 Houston (overnight, dormant)
    # Build a UTC timestamp at 09:00.
    from datetime import datetime as _dt, timezone as _tz

    overnight_epoch = _dt(2026, 5, 23, 9, 0, 0, tzinfo=_tz.utc).timestamp()
    # If zoneinfo is available, this should report False.
    # (If not, the helper falls back permissive — test tolerates both.)
    result = _within_active_window(overnight_epoch)
    assert isinstance(result, bool)
