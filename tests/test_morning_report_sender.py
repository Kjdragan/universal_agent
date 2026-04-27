"""
Tests for the morning report sender service.

Validates:
- is_report_due() time-window and already-sent checks
- build_morning_email_body() content assembly
- MorningReportSender.send_if_due() e2e with mock AgentMail
- Feature flag / env var behavior
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent import task_hub
from universal_agent.services.morning_report_sender import (
    MorningReportSender,
    _already_sent_today,
    _get_overnight_activity,
    _get_recipient_email,
    _is_morning_report_enabled,
    _mark_sent_today,
    build_morning_email_body,
    build_morning_email_subject,
    is_report_due,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory SQLite connection with Task Hub schema."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    return c


@pytest.fixture
def now_7am():
    """Datetime at 7:00 AM"""
    return datetime(2026, 3, 27, 7, 0, 0)


@pytest.fixture
def now_10am():
    """Datetime at 10:00 AM"""
    return datetime(2026, 3, 27, 10, 0, 0)


@pytest.fixture
def now_2am():
    """Datetime at 2:00 AM"""
    return datetime(2026, 3, 27, 2, 0, 0)


@pytest.fixture(autouse=True)
def clean_env():
    """Ensure clean env state for each test."""
    keys = [
        "UA_MORNING_REPORT_ENABLED",
        "UA_MORNING_REPORT_EMAIL",
        "UA_PRIMARY_EMAIL",
        "UA_NOTIFICATION_EMAIL",
        "UA_MORNING_REPORT_HOUR",
        "UA_HEARTBEAT_AUTONOMOUS_ENABLED",
    ]
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    # Default: enable autonomous
    os.environ["UA_HEARTBEAT_AUTONOMOUS_ENABLED"] = "1"
    yield
    for k in keys:
        os.environ.pop(k, None)
        if old[k] is not None:
            os.environ[k] = old[k]


# ---------------------------------------------------------------------------
# is_report_due() tests
# ---------------------------------------------------------------------------

class TestIsReportDue:
    def test_due_at_7am(self, conn, now_7am):
        assert is_report_due(conn, now=now_7am) is True

    def test_not_due_at_10am(self, conn, now_10am):
        assert is_report_due(conn, now=now_10am) is False

    def test_not_due_at_2am(self, conn, now_2am):
        assert is_report_due(conn, now=now_2am) is False

    def test_custom_hour(self, conn):
        os.environ["UA_MORNING_REPORT_HOUR"] = "9"
        now_9am = datetime(2026, 3, 27, 9, 15, 0)
        assert is_report_due(conn, now=now_9am) is True

    def test_already_sent_today(self, conn, now_7am):
        _mark_sent_today(conn)
        assert is_report_due(conn, now=now_7am) is False

    def test_disabled(self, conn, now_7am):
        os.environ["UA_MORNING_REPORT_ENABLED"] = "false"
        assert is_report_due(conn, now=now_7am) is False


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

class TestMorningReportEnabled:
    def test_explicitly_enabled(self):
        os.environ["UA_MORNING_REPORT_ENABLED"] = "true"
        assert _is_morning_report_enabled() is True

    def test_explicitly_disabled(self):
        os.environ["UA_MORNING_REPORT_ENABLED"] = "false"
        assert _is_morning_report_enabled() is False

    def test_follows_autonomous(self):
        os.environ["UA_HEARTBEAT_AUTONOMOUS_ENABLED"] = "1"
        assert _is_morning_report_enabled() is True

    def test_follows_autonomous_disabled(self):
        os.environ.pop("UA_MORNING_REPORT_ENABLED", None)
        os.environ["UA_HEARTBEAT_AUTONOMOUS_ENABLED"] = "0"
        assert _is_morning_report_enabled() is False


# ---------------------------------------------------------------------------
# Recipient email
# ---------------------------------------------------------------------------

class TestGetRecipientEmail:
    def test_specific_report_email(self):
        os.environ["UA_MORNING_REPORT_EMAIL"] = "test@example.com"
        assert _get_recipient_email() == "test@example.com"

    def test_fallback_primary(self):
        os.environ["UA_PRIMARY_EMAIL"] = "primary@example.com"
        assert _get_recipient_email() == "primary@example.com"

    def test_fallback_notification(self):
        os.environ["UA_NOTIFICATION_EMAIL"] = "notify@example.com"
        assert _get_recipient_email() == "notify@example.com"

    def test_no_email_configured(self):
        assert _get_recipient_email() == ""


# ---------------------------------------------------------------------------
# Already-sent tracking
# ---------------------------------------------------------------------------

class TestSentTracking:
    def test_not_sent_initially(self, conn):
        assert _already_sent_today(conn) is False

    def test_mark_and_check(self, conn):
        _mark_sent_today(conn)
        assert _already_sent_today(conn) is True


# ---------------------------------------------------------------------------
# Overnight activity
# ---------------------------------------------------------------------------

class TestOvernightActivity:
    def test_empty_hub(self, conn):
        result = _get_overnight_activity(conn)
        assert result["total_overnight_updates"] == 0
        assert result["completed_overnight"] == []

    def test_recent_completions(self, conn):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        task_hub.upsert_item(conn, {
            "task_id": "overnight-1",
            "title": "Overnight task",
            "status": "completed",
            "priority": 2,
            "source_kind": "brainstorm",
        })
        # Force the updated_at to be recent
        conn.execute(
            "UPDATE task_hub_items SET updated_at = ? WHERE task_id = ?",
            (recent, "overnight-1"),
        )
        result = _get_overnight_activity(conn)
        assert result["total_overnight_updates"] >= 1
        assert len(result["completed_overnight"]) >= 1


# ---------------------------------------------------------------------------
# Email body builder
# ---------------------------------------------------------------------------

class TestBuildMorningEmailBody:
    def test_basic_body(self, conn):
        body = build_morning_email_body(conn)
        assert "Good morning" in body
        assert "OVERNIGHT AUTONOMOUS ACTIVITY" in body
        assert "TASK HUB STATE" in body
        assert "Simone" in body

    def test_with_tasks(self, conn):
        task_hub.upsert_item(conn, {
            "task_id": "test-task-1",
            "title": "Research competitor pricing",
            "status": "open",
            "priority": 2,
            "source_kind": "dashboard_quick_add",
        })
        body = build_morning_email_body(conn)
        assert "Active tasks:" in body

    def test_with_brainstorms(self, conn):
        task_hub.upsert_item(conn, {
            "task_id": "brainstorm-1",
            "title": "API rate limiting strategy",
            "status": "open",
            "priority": 3,
            "source_kind": "brainstorm",
            "refinement_stage": "interviewing",
        })
        body = build_morning_email_body(conn)
        assert "BRAINSTORM PIPELINE" in body or "Active tasks:" in body


class TestBuildMorningEmailSubject:
    def test_subject_format(self):
        subject = build_morning_email_subject()
        assert "Morning Report" in subject
        assert "☀️" in subject
        # Contains a date
        assert "202" in subject


# ---------------------------------------------------------------------------
# MorningReportSender.send_if_due() — e2e with mock AgentMail
# ---------------------------------------------------------------------------

class TestMorningReportSender:
    @pytest.mark.asyncio
    async def test_sends_when_due(self, conn, now_7am):
        mock_mail = AsyncMock()
        mock_mail.send_email = AsyncMock(return_value={
            "status": "sent",
            "message_id": "msg_abc123",
        })
        os.environ["UA_PRIMARY_EMAIL"] = "kevin@example.com"

        sender = MorningReportSender(
            agentmail_service=mock_mail,
            task_hub_db_path="",
        )

        result = await sender.send_if_due(conn=conn, now=now_7am)
        assert result["status"] == "sent"
        assert result["sent"] is True
        assert result["recipient"] == "kevin@example.com"
        mock_mail.send_email.assert_called_once()

        # Check the call args
        call_kwargs = mock_mail.send_email.call_args[1]
        assert call_kwargs["to"] == "kevin@example.com"
        assert "Morning Report" in call_kwargs["subject"]
        assert "Good morning" in call_kwargs["text"]
        assert call_kwargs["force_send"] is True
        assert "morning-report" in call_kwargs["labels"]

    @pytest.mark.asyncio
    async def test_not_due(self, conn, now_10am):
        mock_mail = AsyncMock()
        sender = MorningReportSender(
            agentmail_service=mock_mail,
            task_hub_db_path="",
        )
        result = await sender.send_if_due(conn=conn, now=now_10am)
        assert result["status"] == "not_due"
        assert result["sent"] is False
        mock_mail.send_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_recipient(self, conn, now_7am):
        mock_mail = AsyncMock()
        sender = MorningReportSender(
            agentmail_service=mock_mail,
            task_hub_db_path="",
        )
        result = await sender.send_if_due(conn=conn, now=now_7am)
        assert result["status"] == "no_recipient"
        assert result["sent"] is False

    @pytest.mark.asyncio
    async def test_no_agentmail(self, conn, now_7am):
        os.environ["UA_PRIMARY_EMAIL"] = "kevin@example.com"
        sender = MorningReportSender(
            agentmail_service=None,
            task_hub_db_path="",
        )
        result = await sender.send_if_due(conn=conn, now=now_7am)
        assert result["status"] == "no_agentmail"
        assert result["sent"] is False

    @pytest.mark.asyncio
    async def test_send_failure(self, conn, now_7am):
        mock_mail = AsyncMock()
        mock_mail.send_email = AsyncMock(side_effect=RuntimeError("API down"))
        os.environ["UA_PRIMARY_EMAIL"] = "kevin@example.com"

        sender = MorningReportSender(
            agentmail_service=mock_mail,
            task_hub_db_path="",
        )
        result = await sender.send_if_due(conn=conn, now=now_7am)
        assert result["status"] == "send_failed"
        assert result["sent"] is False
        assert "API down" in result["error"]

    @pytest.mark.asyncio
    async def test_no_double_send(self, conn, now_7am):
        mock_mail = AsyncMock()
        mock_mail.send_email = AsyncMock(return_value={"status": "sent", "message_id": "msg_1"})
        os.environ["UA_PRIMARY_EMAIL"] = "kevin@example.com"

        sender = MorningReportSender(
            agentmail_service=mock_mail,
            task_hub_db_path="",
        )

        # First send succeeds
        result1 = await sender.send_if_due(conn=conn, now=now_7am)
        assert result1["sent"] is True

        # Second send same day — should be blocked
        result2 = await sender.send_if_due(conn=conn, now=now_7am)
        assert result2["status"] == "not_due"
        assert result2["sent"] is False

        # Only one send call total
        assert mock_mail.send_email.call_count == 1

    @pytest.mark.asyncio
    async def test_force_send_bypasses_checks(self, conn, now_10am):
        mock_mail = AsyncMock()
        mock_mail.send_email = AsyncMock(return_value={"status": "sent", "message_id": "msg_f"})
        os.environ["UA_PRIMARY_EMAIL"] = "kevin@example.com"

        sender = MorningReportSender(
            agentmail_service=mock_mail,
            task_hub_db_path="",
        )

        result = await sender.send_forced(conn=conn)
        assert result["sent"] is True
        mock_mail.send_email.assert_called_once()
