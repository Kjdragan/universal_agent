from __future__ import annotations

from csi_ingester.store import source_state
from csi_ingester.store.sqlite import connect, ensure_schema


def test_source_state_roundtrip(tmp_path):
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)
    migration_rows = conn.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id").fetchall()
    assert [str(row["migration_id"]) for row in migration_rows] == ["0001_core", "0002_source_state"]
    source_state.set_state(conn, "youtube_playlist:PL1", {"seeded": True, "seen_ids": ["a", "b"]})
    state = source_state.get_state(conn, "youtube_playlist:PL1")
    assert state == {"seeded": True, "seen_ids": ["a", "b"]}
