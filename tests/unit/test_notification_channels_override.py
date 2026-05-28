"""Per-record channel override on `_add_notification`.

Operational signals that are real but self-healing / low-actionability
(daemon execution-timeout reaps, session continuity-metric alerts) should
be surfaced on the dashboard WITHOUT emailing/Telegram-ing the operator.
`_add_notification(..., channels=["dashboard"])` does that; omitting the
arg keeps the global ops-config targets (dashboard+email+telegram).

These tests exercise the real emitter against a tmp activity DB.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def activity_db(tmp_path, monkeypatch):
    db_path = tmp_path / "activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))
    return str(db_path)


def test_channels_override_dashboard_only(activity_db):
    """An explicit channels=["dashboard"] keeps the record off the email
    and telegram channels — so the dispatcher will never email it."""
    from universal_agent.gateway_server import _add_notification

    record = _add_notification(
        kind="continuity_alert",
        title="Session Continuity Alert",
        message="Resume failures exceeded warning threshold. actual=3, threshold=3",
        severity="warning",
        requires_action=False,
        metadata={"code": "resume_failures_high", "source": "session_continuity_metrics"},
        channels=["dashboard"],
    )
    assert record["channels"] == ["dashboard"]
    assert "email" not in record["channels"]
    assert "telegram" not in record["channels"]


def test_channels_none_uses_default_targets_with_email(activity_db):
    """Omitting channels falls back to the global ops targets, which
    include the email channel (so ordinary alerts still email)."""
    from universal_agent.gateway_server import _add_notification

    record = _add_notification(
        kind="cancelled",
        title="Session Cancelled",
        message="Internal error: provider unavailable",
        severity="warning",
        metadata={"source": "ops"},
    )
    assert "dashboard" in record["channels"]
    assert "email" in record["channels"]


def test_channels_override_is_normalized(activity_db):
    """Mixed-case / whitespace channel names are lowercased and stripped,
    and empty entries dropped."""
    from universal_agent.gateway_server import _add_notification

    record = _add_notification(
        kind="cancelled",
        title="Session Cancelled",
        message="daemon_execution_timeout:1800s",
        severity="warning",
        metadata={"source": "ops"},
        channels=["Dashboard", "  ", " telegram "],
    )
    assert record["channels"] == ["dashboard", "telegram"]
