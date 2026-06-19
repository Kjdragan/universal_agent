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
        "INSERT INTO dedupe_keys(key, expires_at) VALUES ('threads:old', datetime('now', '-1 day'))"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    findings = db_health_monitor.check_csi_dedupe_bloat()

    assert len(findings) == 1
    assert findings[0].finding_id == "csi_dedupe_expired_keys_unpurged"
    assert findings[0].observed_value == 1


def _make_source_state_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE source_state ("
        " source_key TEXT PRIMARY KEY,"
        " state_json TEXT NOT NULL,"
        " updated_at TEXT DEFAULT (datetime('now')))"
    )
    return conn


def test_csi_source_freshness_recent_channels_not_flagged(monkeypatch, tmp_path) -> None:
    # Regression: cutoff used .isoformat() ("...T...+00:00") while updated_at is
    # stored as "YYYY-MM-DD HH:MM:SS"; the lexicographic compare flagged every
    # fresh channel as stale. Recently-polled channels must produce no findings.
    db_path = tmp_path / "csi.db"
    conn = _make_source_state_db(db_path)
    conn.executemany(
        "INSERT INTO source_state(source_key, state_json, updated_at)"
        " VALUES (?, '{}', datetime('now'))",
        [(f"youtube_channel_rss:UC{idx}",) for idx in range(20)],
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    assert db_health_monitor.check_csi_source_freshness() == []


def test_csi_source_freshness_all_stale_channels_flagged(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "csi.db"
    conn = _make_source_state_db(db_path)
    stale_offset = f"-{int(db_health_monitor.CSI_SOURCE_STALE_HOURS) + 2} hours"
    conn.executemany(
        "INSERT INTO source_state(source_key, state_json, updated_at)"
        " VALUES (?, '{}', datetime('now', ?))",
        [(f"youtube_channel_rss:UC{idx}", stale_offset) for idx in range(20)],
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    findings = db_health_monitor.check_csi_source_freshness()

    assert len(findings) == 1
    assert findings[0].finding_id == "csi_rss_all_channels_stale"
    assert findings[0].observed_value == 20



# ── Steady-state-aware check_pending_signal_cards tests ───────────────
# Both directions: healthy promote-zero steady state must NOT fire, while a
# genuine backlog burst or a stopped curator MUST fire.


def _make_activity_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE proactive_signal_cards ("
        " card_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def _insert_pending_cards(path: Path, count: int, age_hours: float,
                          *, start_index: int = 0) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    created = (now - timedelta(hours=age_hours)).isoformat()
    conn = sqlite3.connect(str(path))
    conn.executemany(
        "INSERT INTO proactive_signal_cards(card_id, status, created_at, updated_at)"
        " VALUES (?, 'pending', ?, ?)",
        [(f"card-{start_index + i}", created, created) for i in range(count)],
    )
    conn.commit()
    conn.close()


def test_signal_cards_steady_state_not_flagged(monkeypatch, tmp_path) -> None:
    # Documented healthy promote-zero steady state: ~287 pending cards with the
    # oldest well under the hourly curation cadence. The old >=50 rule fired
    # here every single heartbeat; the steady-state-aware check must NOT.
    db_path = tmp_path / "activity_state.db"
    _make_activity_db(db_path)
    _insert_pending_cards(db_path, count=287, age_hours=0.5)
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))

    assert db_health_monitor.check_pending_signal_cards() == []


def test_signal_cards_backlog_burst_flagged(monkeypatch, tmp_path) -> None:
    # Creation severely outpacing curation: count blows past the working set
    # even though the oldest card is still fresh. Fires via the count branch.
    db_path = tmp_path / "activity_state.db"
    _make_activity_db(db_path)
    burst = db_health_monitor.SIGNAL_CARDS_PENDING_WARN + 100
    _insert_pending_cards(db_path, count=burst, age_hours=0.2)
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))

    findings = db_health_monitor.check_pending_signal_cards()

    assert len(findings) == 1
    assert findings[0].finding_id == "signal_cards_backlog"
    assert findings[0].metric_key == "activity.pending_signal_cards"
    assert findings[0].observed_value == burst


def test_signal_cards_curator_stalled_flagged(monkeypatch, tmp_path) -> None:
    # Modest count (well under 500) but the oldest pending card is far past
    # the hourly curation cadence => curator stopped processing. Must fire on
    # age alone even though the count branch would not.
    db_path = tmp_path / "activity_state.db"
    _make_activity_db(db_path)
    stall_age = db_health_monitor.SIGNAL_CARDS_STALE_HOURS + 2.0
    _insert_pending_cards(db_path, count=50, age_hours=stall_age)
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))

    findings = db_health_monitor.check_pending_signal_cards()

    assert len(findings) == 1
    assert findings[0].finding_id == "signal_cards_backlog"
    assert findings[0].observed_value == 50


def test_signal_cards_empty_not_flagged(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "activity_state.db"
    _make_activity_db(db_path)
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))

    assert db_health_monitor.check_pending_signal_cards() == []


# ── Steady-state-aware check_csi_dedupe_bloat tests ───────────────────
# Both directions: a transient handful of <1h-old expired keys between hourly
# sweeps must NOT fire, while a stale-purge failure or sustained fraction MUST.


def _insert_dedupe_keys(path: Path, expired_offsets: list[str],
                        active_count: int) -> None:
    """Insert expired keys (each with a sqlite datetime offset like '-30 minutes')
    plus ``active_count`` far-future keys into the dedupe_keys table."""
    conn = sqlite3.connect(str(path))
    conn.executemany(
        "INSERT INTO dedupe_keys(key, expires_at) VALUES (?, datetime('now', ?))",
        [(f"exp-{i}", offset) for i, offset in enumerate(expired_offsets)],
    )
    conn.executemany(
        "INSERT INTO dedupe_keys(key, expires_at) VALUES (?, datetime('now', '+30 days'))",
        [(f"active-{i}",) for i in range(active_count)],
    )
    conn.commit()
    conn.close()


def test_csi_dedupe_steady_state_transient_expired_not_flagged(
    monkeypatch, tmp_path
) -> None:
    # Documented healthy steady state: 28 <1h-old expired keys against a ~60k
    # table (28/60098 = 0.04%). The old ">0 expired" rule fired every
    # heartbeat; the steady-state-aware check must NOT.
    db_path = tmp_path / "csi.db"
    _make_csi_db(db_path)
    _insert_dedupe_keys(
        db_path,
        expired_offsets=["-30 minutes"] * 28,
        active_count=60_098,
    )
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    assert db_health_monitor.check_csi_dedupe_bloat() == []


def test_csi_dedupe_stale_fraction_flagged(monkeypatch, tmp_path) -> None:
    # >5% of a production-scale table expired => sustained backlog. Fires via
    # the fraction branch even though no single key is past the age threshold.
    db_path = tmp_path / "csi.db"
    _make_csi_db(db_path)
    _insert_dedupe_keys(
        db_path,
        expired_offsets=["-30 minutes"] * 100,
        active_count=900,
    )
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    findings = db_health_monitor.check_csi_dedupe_bloat()

    assert len(findings) == 1
    assert findings[0].finding_id == "csi_dedupe_expired_keys_unpurged"
    assert findings[0].metric_key == "csi.dedupe_keys_expired_count"
    assert findings[0].observed_value == 100


def test_csi_dedupe_stale_age_flagged(monkeypatch, tmp_path) -> None:
    # Oldest expired key far past the hourly purge cadence => purge loop
    # stopped. Fires via the age branch even though the fraction is tiny.
    db_path = tmp_path / "csi.db"
    _make_csi_db(db_path)
    stale_age = f"-{int(db_health_monitor.CSI_DEDUPE_STALE_HOURS) + 2} hours"
    _insert_dedupe_keys(
        db_path,
        expired_offsets=[stale_age] * 5,
        active_count=10_000,
    )
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    findings = db_health_monitor.check_csi_dedupe_bloat()

    assert len(findings) == 1
    assert findings[0].finding_id == "csi_dedupe_expired_keys_unpurged"
    assert findings[0].observed_value == 5


def test_csi_dedupe_tiny_table_high_fraction_not_flagged(
    monkeypatch, tmp_path
) -> None:
    # A small table can show a high fraction without being a real backlog; the
    # fraction signal only applies at production scale. With oldest <4h and
    # total < FRACTION_MIN_TOTAL, this MUST NOT fire.
    db_path = tmp_path / "csi.db"
    _make_csi_db(db_path)
    _insert_dedupe_keys(
        db_path,
        expired_offsets=["-30 minutes"] * 5,
        active_count=5,
    )
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))

    assert db_health_monitor.check_csi_dedupe_bloat() == []
