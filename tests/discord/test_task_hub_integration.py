from datetime import UTC, datetime
import sqlite3

from discord_intelligence.database import DiscordIntelligenceDB
from discord_intelligence.integration import task_hub as discord_task_hub


def test_create_task_hub_mission_sets_discord_source_and_labels(monkeypatch, tmp_path):
    db_path = tmp_path / "activity_state.db"

    monkeypatch.setattr(discord_task_hub, "init_secrets", lambda: None)
    monkeypatch.setattr(discord_task_hub, "get_activity_db_path", lambda: str(db_path))

    task_id = discord_task_hub.create_task_hub_mission(
        title="Discord CC Task",
        description="Run this from Discord",
        tags=["simone-chat", "direct-prompt"],
        source_kind="discord_command",
        metadata={"message_id": "123"},
    )

    assert task_id

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT source_kind, labels_json, metadata_json, agent_ready FROM task_hub_items WHERE task_id = ?",
            (task_id,),
        ).fetchone()

    assert dict(row)["source_kind"] == "discord_command"
    assert dict(row)["agent_ready"] == 1
    assert "simone-chat" in dict(row)["labels_json"]
    assert "discord_command" in dict(row)["metadata_json"]
    assert "123" in dict(row)["metadata_json"]


def test_discord_db_tracks_calendar_sync_and_channel_tuning(tmp_path):
    db_path = tmp_path / "discord_intelligence.db"
    db = DiscordIntelligenceDB(str(db_path))

    db.upsert_server("server-1", "Example Server")
    db.upsert_channel("channel-1", "server-1", "events", "Announcements")
    db.upsert_scheduled_event(
        event_id="event-1",
        server_id="server-1",
        name="Office Hours",
        description="Bring questions",
        start_time=datetime.fromisoformat("2026-05-01T15:00:00+00:00"),
        end_time=None,
        location=None,
        status="scheduled",
        entity_type="stage_instance",
        channel_id="channel-1",
        creator_name="organizer",
        user_count=4,
        discord_event_url="https://discord.com/events/server-1/event-1",
    )

    candidates = db.get_calendar_sync_candidates(limit=10)
    assert [row["id"] for row in candidates] == ["event-1"]

    db.mark_event_calendar_synced("event-1", "discordevent1", "2026-04-10T00:00:00+00:00")
    assert db.get_calendar_sync_candidates(limit=10) == []
    assert db.count_calendar_synced_today() == 0

    db.mark_event_calendar_failed("event-1", "temporary calendar auth failure")
    assert db.get_calendar_sync_candidates(limit=10) == []
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE scheduled_events SET calendar_last_attempt_at = NULL WHERE id = ?", ("event-1",))
        conn.commit()
    assert [row["id"] for row in db.get_calendar_sync_candidates(limit=10)] == ["event-1"]

    db.mark_event_calendar_synced("event-1", "discordevent1", datetime.now(UTC).isoformat())
    assert db.count_calendar_synced_today() == 1

    db.update_channel_config("channel-1", tier="A", is_active=False)
    overview = db.channel_overview(limit=10)
    row = next(item for item in overview if item["id"] == "channel-1")
    assert row["tier"] == "A"
    assert row["is_active"] == 0
