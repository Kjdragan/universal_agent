from __future__ import annotations

from csi_ingester.store.sqlite import connect


def test_default_busy_timeout(tmp_path):
    conn = connect(tmp_path / "csi.db")
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert int(row[0]) == 15000


def test_env_override_busy_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_SQLITE_BUSY_TIMEOUT_MS", "5000")
    conn = connect(tmp_path / "csi.db")
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert int(row[0]) == 5000


def test_default_journal_mode_is_wal(tmp_path):
    conn = connect(tmp_path / "csi.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    # WAL pairs with synchronous=NORMAL (== 1).
    assert int(conn.execute("PRAGMA synchronous").fetchone()[0]) == 1


def test_env_override_journal_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_SQLITE_JOURNAL_MODE", "DELETE")
    conn = connect(tmp_path / "csi.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"


def test_empty_journal_mode_leaves_db_default(tmp_path, monkeypatch):
    # Empty disables the pragma entirely; a fresh db stays on the sqlite default.
    monkeypatch.setenv("CSI_SQLITE_JOURNAL_MODE", "")
    conn = connect(tmp_path / "csi.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
