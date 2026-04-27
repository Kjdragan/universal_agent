from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def test_agent_activity_new_counts_created_tasks_not_defer_events() -> None:
    conn = _conn()
    try:
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=3)

        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:new:1",
                "source_kind": "system_command",
                "title": "New task",
                "project_key": "immediate",
                "agent_ready": True,
                "created_at": now.isoformat(),
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:old:1",
                "source_kind": "csi",
                "title": "Old task",
                "project_key": "csi",
                "agent_ready": True,
                "created_at": old.isoformat(),
            },
        )

        # Simulate scorer churn that previously inflated "new".
        for _ in range(50):
            conn.execute(
                """
                INSERT INTO task_hub_evaluations (
                    id, task_id, evaluated_at, agent_id, decision, reason, score, score_confidence, judge_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"eval_{_}",
                    "task:new:1",
                    now.isoformat(),
                    "scorer",
                    "defer",
                    "dispatch_rebuild",
                    6.0,
                    0.58,
                    "{}",
                ),
            )
        conn.commit()

        activity = task_hub.get_agent_activity(conn)
        assert activity["metrics"]["1h"]["new"] == 1
        assert activity["metrics"]["1h"]["new_by_source"]["system_command"] == 1
        assert activity["metrics"]["1h"].get("seized") == 0
    finally:
        conn.close()

