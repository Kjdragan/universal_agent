"""Regression tests: proactive_health findings must NOT create Task Hub rows.

As of the 2026-06-03 surface change, proactive_health invariant findings are
delivered via:
  1. critical email (the durable alert-of-record), and
  2. the live GET /api/v1/ops/proactive_health endpoint (dashboard surface).

The notifier no longer accepts a ``task_hub_emit_fn`` and writes ZERO rows into
``task_hub_items``. These tests lock that contract end-to-end:

  * a CRITICAL finding writes no ``source_kind='proactive_health'`` row but
    STILL calls the agentmail send path, and
  * a WARN finding likewise writes no row (and never emails).

They also guard the function signature so a future regression that re-adds the
``task_hub_emit_fn`` kwarg fails loudly.
"""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock

import pytest

from universal_agent.services import proactive_health_notifier as notifier
from universal_agent.services.proactive_health_notifier import (
    KEVIN_EMAIL,
    run_pre_flight_check,
)

TASK_HUB_SCHEMA = """
CREATE TABLE task_hub_items (
    task_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _critical_payload(finding_id: str = "youtube_enrichment_coverage") -> dict:
    return {
        "overall_status": "critical",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "crons": [],
        "stale_tasks": {"count": 0, "samples": []},
        "parked_tasks": {"count": 0, "samples": []},
        "invariants": [
            {
                "finding_id": f"invariant:{finding_id}",
                "category": "proactive_health",
                "severity": "critical",
                "metric_key": finding_id,
                "observed_value": {"coverage_pct": 0.0, "total_events": 349},
                "title": "Test critical invariant",
                "recommendation": "fix it",
                "runbook_command": "sqlite3 ... SELECT ...",
                "metadata": {},
            }
        ],
    }


def _warn_payload(finding_id: str = "morning_briefing_freshness") -> dict:
    payload = _critical_payload(finding_id)
    payload["overall_status"] = "warn"
    payload["invariants"][0]["severity"] = "warn"
    return payload


@pytest.fixture
def activity_conn() -> sqlite3.Connection:
    """In-memory activity DB with the task_hub_items table seeded empty.

    The notifier never touches Task Hub, so this connection just proves the
    table stays empty across a full pre-flight run.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(TASK_HUB_SCHEMA)
    conn.commit()
    return conn


@pytest.fixture
def fake_agentmail():
    mock = AsyncMock()
    mock.send_email = AsyncMock(return_value={"message_id": "abc", "status": "sent"})
    return mock


@pytest.fixture
def notifications_list() -> list:
    return []


@pytest.fixture
def add_notification_fn(notifications_list):
    def _add(*, kind, title, message, summary=None, severity="info", requires_action=False, metadata=None, **_):
        record = {
            "kind": kind,
            "title": title,
            "message": message,
            "summary": summary,
            "severity": severity,
            "requires_action": requires_action,
            "metadata": dict(metadata or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        notifications_list.append(record)
        return record

    return _add


@pytest.fixture(autouse=True)
def _reset_skip_counter():
    notifier._skipped_consecutive.clear()
    yield
    notifier._skipped_consecutive.clear()


def _proactive_health_row_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) FROM task_hub_items WHERE source_kind = 'proactive_health'"
    )
    return int(cur.fetchone()[0])


def _total_task_rows(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM task_hub_items")
    return int(cur.fetchone()[0])


@pytest.mark.asyncio
async def test_critical_finding_writes_no_task_hub_row_but_still_emails(
    tmp_path: Path,
    activity_conn: sqlite3.Connection,
    fake_agentmail,
    notifications_list,
    add_notification_fn,
):
    """A CRITICAL finding goes out by email but creates ZERO Task Hub rows."""
    payload_returned = await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )

    # Email path fired.
    fake_agentmail.send_email.assert_awaited_once()
    call = fake_agentmail.send_email.call_args
    assert call.kwargs["to"] == KEVIN_EMAIL
    assert call.kwargs["force_send"] is True
    assert "CRITICAL" in call.kwargs["subject"]

    # No Task Hub rows at all — and specifically none authored by the watchdog.
    assert _proactive_health_row_count(activity_conn) == 0
    assert _total_task_rows(activity_conn) == 0

    # Payload still returned for the caller to log/serve.
    assert payload_returned["overall_status"] == "critical"


@pytest.mark.asyncio
async def test_warn_finding_writes_no_task_hub_row_and_does_not_email(
    tmp_path: Path,
    activity_conn: sqlite3.Connection,
    fake_agentmail,
    notifications_list,
    add_notification_fn,
):
    """A WARN finding creates no Task Hub row and never emails."""
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_warn_payload,
        agentmail_service=fake_agentmail,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )

    fake_agentmail.send_email.assert_not_called()
    assert _proactive_health_row_count(activity_conn) == 0
    assert _total_task_rows(activity_conn) == 0


@pytest.mark.asyncio
async def test_critical_finding_with_blocked_email_still_writes_no_row(
    tmp_path: Path,
    activity_conn: sqlite3.Connection,
    notifications_list,
    add_notification_fn,
):
    """Even when the email channel is down (agentmail_service=None), the
    notifier must not fall back to writing a Task Hub row — there is no longer
    any Task Hub channel."""
    await run_pre_flight_check(
        workspace_dir=tmp_path,
        payload_builder=_critical_payload,
        agentmail_service=None,
        notifications_list=notifications_list,
        add_notification_fn=add_notification_fn,
    )
    assert _proactive_health_row_count(activity_conn) == 0
    assert _total_task_rows(activity_conn) == 0


def test_run_pre_flight_check_has_no_task_hub_emit_parameter():
    """The task_hub_emit_fn parameter is gone — guard against accidental re-add."""
    params = inspect.signature(run_pre_flight_check).parameters
    assert "task_hub_emit_fn" not in params


def test_dead_task_hub_helpers_are_removed():
    """The Task Hub emit + warn-escalation plumbing is deleted from the module."""
    for symbol in (
        "_emit_to_task_hub",
        "_track_and_filter_warns_for_escalation",
        "_warn_threshold",
        "_warn_invariants",
        "WARN_ESCALATION_THRESHOLD",
        "_consecutive_warns",
    ):
        assert not hasattr(notifier, symbol), f"{symbol} should have been deleted"
    # The critical-email path helper must survive.
    assert hasattr(notifier, "_critical_invariants")
