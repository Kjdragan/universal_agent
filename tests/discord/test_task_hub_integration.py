import sqlite3

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
