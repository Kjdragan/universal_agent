"""
test_proactive_advisor.py — Unit tests for the proactive advisor service.

Tests the deterministic morning report builder, brainstorm context assembly,
and prompt formatting functions.
"""

from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import patch

import pytest

from universal_agent import task_hub
from universal_agent.services.proactive_advisor import (
    _parse_iso_age_hours,
    build_brainstorm_context,
    build_morning_report,
    format_brainstorm_context_prompt,
    format_morning_report_prompt,
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


def _insert_task(conn, task_id, title, status="open", refinement_stage=None, updated_at=None):
    """Helper to insert a task directly into the DB."""
    if updated_at is None:
        updated_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO task_hub_items "
        "(task_id, source_kind, title, description, status, refinement_stage, updated_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, "test", title, "", status, refinement_stage, updated_at, updated_at),
    )
    conn.commit()


def _insert_question(conn, question_id, task_id, text, answered=0, expires_at=None):
    """Helper to insert a question into the queue."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO task_hub_question_queue "
        "(question_id, task_id, question_text, answered, expires_at, asked_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (question_id, task_id, text, answered, expires_at, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _parse_iso_age_hours
# ---------------------------------------------------------------------------

class TestParseIsoAgeHours:
    def test_none_returns_none(self):
        assert _parse_iso_age_hours(None) is None

    def test_empty_returns_none(self):
        assert _parse_iso_age_hours("") is None

    def test_garbage_returns_none(self):
        assert _parse_iso_age_hours("not-a-date") is None

    def test_recent_timestamp_returns_small_hours(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        hours = _parse_iso_age_hours(recent)
        assert hours is not None
        assert 0.3 < hours < 1.0

    def test_old_timestamp(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        hours = _parse_iso_age_hours(old)
        assert hours is not None
        assert 71 < hours < 73

    def test_z_suffix(self):
        ts = "2020-01-01T00:00:00Z"
        hours = _parse_iso_age_hours(ts)
        assert hours is not None
        assert hours > 24  # definitely old


# ---------------------------------------------------------------------------
# build_morning_report
# ---------------------------------------------------------------------------

class TestBuildMorningReport:
    def test_empty_hub_returns_zeroes(self, conn):
        report = build_morning_report(conn)
        assert report["total_active"] == 0
        assert report["brainstorm_tasks"] == []
        assert report["stale_in_progress"] == []
        assert report["unanswered_questions_count"] == 0
        assert report["expiring_questions_count"] == 0
        assert isinstance(report["report_text"], str)
        assert "Morning Report" in report["report_text"]

    def test_counts_active_tasks(self, conn):
        _insert_task(conn, "t1", "Task One", status="open")
        _insert_task(conn, "t2", "Task Two", status="in_progress")
        _insert_task(conn, "t3", "Done Task", status="done")
        report = build_morning_report(conn)
        assert report["total_active"] == 2  # t3 is done, excluded

    def test_detects_brainstorm_tasks(self, conn):
        _insert_task(conn, "b1", "Brainstorm Task", status="in_progress",
                     refinement_stage="idea")
        report = build_morning_report(conn)
        assert len(report["brainstorm_tasks"]) == 1
        assert report["brainstorm_tasks"][0]["stage"] == "idea"

    def test_detects_stale_brainstorm(self, conn):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
        _insert_task(conn, "b2", "Stale Brainstorm", status="in_progress",
                     refinement_stage="research", updated_at=old_ts)
        report = build_morning_report(conn)
        assert len(report["brainstorm_tasks"]) == 1
        assert report["brainstorm_tasks"][0]["is_stale"] is True
        assert report["stale_brainstorm_count"] == 1

    def test_detects_stale_in_progress(self, conn):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        _insert_task(conn, "s1", "Stale Task", status="in_progress",
                     updated_at=old_ts)
        report = build_morning_report(conn)
        assert len(report["stale_in_progress"]) == 1

    def test_counts_unanswered_questions(self, conn):
        _insert_task(conn, "q1", "Q Task", status="open")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        _insert_question(conn, "qq1", "q1", "What color?", expires_at=future)
        _insert_question(conn, "qq2", "q1", "What size?", answered=1, expires_at=future)
        report = build_morning_report(conn)
        assert report["unanswered_questions_count"] == 1

    def test_report_text_is_nonempty(self, conn):
        _insert_task(conn, "t1", "Test Task", status="open")
        report = build_morning_report(conn)
        assert len(report["report_text"]) > 50


# ---------------------------------------------------------------------------
# format_morning_report_prompt
# ---------------------------------------------------------------------------

class TestFormatMorningReportPrompt:
    def test_empty_report(self):
        report = {
            "total_active": 0,
            "brainstorm_tasks": [],
            "stale_in_progress": [],
            "overdue_scheduled": [],
            "unanswered_questions_count": 0,
            "expiring_questions_count": 0,
        }
        text = format_morning_report_prompt(report)
        assert "Morning Report" in text
        assert "Active tasks" in text

    def test_includes_brainstorm_table(self):
        report = {
            "total_active": 2,
            "brainstorm_tasks": [
                {"title": "Brain One", "stage": "idea", "pending_questions": 1, "is_stale": False},
            ],
            "stale_in_progress": [],
            "overdue_scheduled": [],
            "unanswered_questions_count": 1,
            "expiring_questions_count": 0,
        }
        text = format_morning_report_prompt(report)
        assert "Brain One" in text
        assert "idea" in text

    def test_includes_stale_section(self):
        report = {
            "total_active": 1,
            "brainstorm_tasks": [],
            "stale_in_progress": [{"title": "Old Task", "stale_hours": 48.5}],
            "overdue_scheduled": [],
            "unanswered_questions_count": 0,
            "expiring_questions_count": 0,
        }
        text = format_morning_report_prompt(report)
        assert "Old Task" in text
        assert "Stale" in text


# ---------------------------------------------------------------------------
# build_brainstorm_context
# ---------------------------------------------------------------------------

class TestBuildBrainstormContext:
    def test_empty_returns_empty(self, conn):
        ctx = build_brainstorm_context(conn)
        assert ctx == []

    def test_finds_brainstorm_tasks(self, conn):
        _insert_task(conn, "b1", "Brainstorm A", status="in_progress",
                     refinement_stage="ideation")
        _insert_task(conn, "b2", "Not Brainstorm", status="in_progress")
        ctx = build_brainstorm_context(conn)
        assert len(ctx) == 1
        assert ctx[0]["title"] == "Brainstorm A"
        assert ctx[0]["stage"] == "ideation"

    def test_counts_pending_questions(self, conn):
        _insert_task(conn, "b1", "B1", status="in_progress",
                     refinement_stage="research")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        _insert_question(conn, "q1", "b1", "Question 1", expires_at=future)
        _insert_question(conn, "q2", "b1", "Question 2", expires_at=future)
        _insert_question(conn, "q3", "b1", "Answered Q", answered=1, expires_at=future)
        ctx = build_brainstorm_context(conn)
        assert len(ctx) == 1
        assert ctx[0]["pending_questions"] == 2


# ---------------------------------------------------------------------------
# format_brainstorm_context_prompt
# ---------------------------------------------------------------------------

class TestFormatBrainstormContextPrompt:
    def test_empty_returns_empty_string(self):
        assert format_brainstorm_context_prompt([]) == ""

    def test_formats_table(self):
        ctx = [
            {"title": "Task A", "stage": "idea", "pending_questions": 3},
        ]
        text = format_brainstorm_context_prompt(ctx)
        assert "Brainstorm Tasks" in text
        assert "Task A" in text
        assert "idea" in text
        assert "3 unanswered" in text

    def test_includes_instructions(self):
        ctx = [{"title": "T", "stage": "s", "pending_questions": 0}]
        text = format_brainstorm_context_prompt(ctx)
        assert "INSTRUCTIONS" in text
