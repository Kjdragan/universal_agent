from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent import gateway_server


def test_non_actionable_notifications_start_read(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))
    notification = gateway_server._add_notification(
        kind="cron_run_success",
        title="Cron Run Succeeded",
        message="routine success",
        severity="info",
        requires_action=False,
    )

    assert notification["status"] == "read"


def test_activity_prune_auto_reads_old_routine_notifications(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "activity_state.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))
    old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    gateway_server._ensure_activity_schema(conn)
    gateway_server._activity_upsert_record(
        {
            "id": "old-routine",
            "event_class": "notification",
            "source_domain": "cron",
            "kind": "cron_run_success",
            "title": "Cron Run Succeeded",
            "summary": "routine success",
            "full_message": "routine success",
            "severity": "info",
            "status": "new",
            "requires_action": False,
            "created_at": old_ts,
            "updated_at": old_ts,
            "entity_ref": {},
            "actions": [],
            "metadata": {},
            "channels": ["dashboard"],
            "email_targets": [],
        }
    )

    gateway_server._activity_prune_old(conn)

    row = conn.execute("SELECT status, metadata_json FROM activity_events WHERE id = 'old-routine'").fetchone()
    conn.close()
    assert row["status"] == "read"
    assert "non_actionable_notification_ttl" in str(row["metadata_json"])


def test_youtube_success_auto_resolves_all_superseded_failure_kinds(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "activity_state.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))
    gateway_server._notifications.clear()

    gateway_server._activity_upsert_record(
        {
            "id": "failed-progress",
            "event_class": "notification",
            "source_domain": "tutorial",
            "kind": "youtube_tutorial_progress",
            "title": "YouTube Tutorial Progress",
            "summary": "old progress",
            "full_message": "old progress",
            "severity": "warning",
            "status": "new",
            "requires_action": True,
            "created_at": "2026-04-28T00:00:00+00:00",
            "updated_at": "2026-04-28T00:00:00+00:00",
            "entity_ref": {},
            "actions": [],
            "metadata": {"video_id": "recover123"},
            "channels": ["dashboard"],
            "email_targets": [],
        }
    )

    gateway_server._add_notification(
        kind="youtube_tutorial_ready",
        title="YouTube Tutorial Ready",
        message="recovered",
        severity="success",
        metadata={"video_id": "recover123"},
    )

    event = gateway_server._get_activity_event("failed-progress")
    assert event is not None
    assert event["status"] == "resolved"
    assert event["requires_action"] is False
