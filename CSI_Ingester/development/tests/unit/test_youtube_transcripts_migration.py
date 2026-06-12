from __future__ import annotations

from csi_ingester.store.sqlite import connect, ensure_schema


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def test_migration_0014_creates_youtube_transcripts_table(tmp_path):
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)

    # Migration registered + applied.
    applied = {
        str(row["migration_id"])
        for row in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
    }
    assert "0014_youtube_transcripts" in applied

    # Table present with the expected columns.
    assert _table_exists(conn, "youtube_transcripts")
    cols = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(youtube_transcripts)").fetchall()
    }
    assert cols == {
        "video_id",
        "event_id",
        "channel_id",
        "channel_name",
        "title",
        "published_at",
        "language",
        "char_count",
        "transcript_text",
        "source_ref",
        "fetched_at",
    }


def test_migration_0014_is_idempotent(tmp_path):
    db = tmp_path / "csi.db"
    conn = connect(db)
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO youtube_transcripts "
        "(video_id, event_id, channel_id, channel_name, title, published_at, "
        "char_count, transcript_text, source_ref) "
        "VALUES ('vid123', 'evt456', 'chan789', 'Test Channel', 'Test Title', "
        "'2026-06-11T00:00:00Z', 1234, 'full transcript text here', 'ua@127.0.0.1')"
    )
    conn.commit()

    # Re-running ensure_schema must not error or drop data.
    ensure_schema(conn)
    row = conn.execute(
        "SELECT channel_name, char_count, transcript_text "
        "FROM youtube_transcripts WHERE video_id='vid123'"
    ).fetchone()
    assert str(row["channel_name"]) == "Test Channel"
    assert int(row["char_count"]) == 1234
    assert str(row["transcript_text"]) == "full transcript text here"
