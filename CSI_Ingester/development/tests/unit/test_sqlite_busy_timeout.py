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
