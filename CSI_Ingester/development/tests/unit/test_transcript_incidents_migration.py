from __future__ import annotations

from csi_ingester.store.sqlite import connect, ensure_schema


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def test_migration_0013_creates_transcript_incidents_table(tmp_path):
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)

    # Migration registered + applied.
    applied = {
        str(row["migration_id"])
        for row in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
    }
    assert "0013_transcript_incidents" in applied

    # Table present with the expected columns.
    assert _table_exists(conn, "transcript_incidents")
    cols = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(transcript_incidents)").fetchall()
    }
    assert cols == {
        "incident_key",
        "state",
        "first_red_at",
        "last_red_at",
        "opened_epoch",
        "email_count",
        "last_email_epoch",
        "next_email_epoch",
        "resolved_at",
        "last_reason",
    }


def test_migration_0013_is_idempotent(tmp_path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO transcript_incidents (incident_key, state, email_count) "
        "VALUES ('youtube_transcript_red', 'open', 1)"
    )
    conn.commit()

    # Re-running ensure_schema must not error or drop data.
    ensure_schema(conn)
    row = conn.execute(
        "SELECT state, email_count FROM transcript_incidents WHERE incident_key='youtube_transcript_red'"
    ).fetchone()
    assert str(row["state"]) == "open"
    assert int(row["email_count"]) == 1
