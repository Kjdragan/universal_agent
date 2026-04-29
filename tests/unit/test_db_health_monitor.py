from pathlib import Path
import sqlite3

from universal_agent.utils import db_health_monitor


def _make_csi_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE dedupe_keys (key TEXT PRIMARY KEY, expires_at TEXT NOT NULL)")
    conn.execute("CREATE INDEX idx_dedupe_expires ON dedupe_keys(expires_at)")
    conn.commit()
    conn.close()


def test_csi_dedupe_high_active_count_is_not_bloat(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "csi.db"
    _make_csi_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.executemany(
        "INSERT INTO dedupe_keys(key, expires_at) VALUES (?, datetime('now', '+30 days'))",
        [(f"youtube:{idx}",) for idx in range(db_health_monitor.CSI_DEDUPE_BLOAT_WARN + 1)],
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    assert db_health_monitor.check_csi_dedupe_bloat() == []


def test_csi_dedupe_expired_keys_warn(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "csi.db"
    _make_csi_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO dedupe_keys(key, expires_at) VALUES ('reddit:old', datetime('now', '-1 day'))"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    findings = db_health_monitor.check_csi_dedupe_bloat()

    assert len(findings) == 1
    assert findings[0].finding_id == "csi_dedupe_expired_keys_unpurged"
    assert findings[0].observed_value == 1
