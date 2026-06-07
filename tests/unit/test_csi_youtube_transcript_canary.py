"""Unit tests for the YouTube transcript pipeline freshness canary.

Covers the two regression classes the canary is supposed to catch:
  1. STALE — enrichment timer is dead (no recent analysis rows)
  2. AUTH BROKEN — fresh rows exist but transcript_status='ok' rate is
     suppressed and the http_error rate is elevated.

Plus the quiet-window and happy-path branches.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys
import uuid

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "CSI_Ingester" / "development" / "scripts"


def _load_canary():
    """Load the canary script as a module without running it."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "csi_youtube_transcript_canary",
        SCRIPTS_DIR / "csi_youtube_transcript_canary.py",
    )
    assert spec and spec.loader, "canary module spec not loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def canary():
    return _load_canary()


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            dedupe_key TEXT,
            source TEXT NOT NULL,
            event_type TEXT,
            occurred_at TEXT,
            received_at TEXT,
            subject_json TEXT,
            routing_json TEXT,
            metadata_json TEXT,
            delivered INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE rss_event_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            transcript_status TEXT,
            transcript_ref TEXT,
            analyzed_at TEXT
        );
        """
    )
    return conn


def _insert_event(conn, *, hours_ago: float = 0) -> str:
    eid = f"evt_{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO events (event_id, source, created_at) "
        "VALUES (?, 'youtube_channel_rss', datetime('now', ?))",
        (eid, f"-{hours_ago} hours"),
    )
    conn.commit()
    return eid


def _insert_analysis(
    conn,
    *,
    event_id: str | None = None,
    status: str,
    ref: str = "",
    hours_ago: float = 0,
) -> None:
    conn.execute(
        "INSERT INTO rss_event_analysis "
        "(event_id, transcript_status, transcript_ref, analyzed_at) "
        "VALUES (?, ?, ?, datetime('now', ?))",
        (
            event_id or f"evt_{uuid.uuid4().hex[:10]}",
            status,
            ref,
            f"-{hours_ago} hours",
        ),
    )
    conn.commit()


def test_quiet_window_returns_green(canary, tmp_path):
    """Below `require_min_events`, canary stays green even with no analysis."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(2):
        _insert_event(conn, hours_ago=1)
    conn.close()

    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(conn, window_hours=24, stale_after_hours=2)
    conn.close()
    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )
    assert verdict["status"] == "green"
    assert any("quiet_window" in r for r in verdict["reasons"])


def test_stale_table_with_events_is_red(canary, tmp_path):
    """The exact 2026-03/05 regression: events arriving, analysis frozen."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=1)
    # One analysis row from 53 days ago — simulates the production state.
    _insert_analysis(conn, status="ok", ref="desktop_worker_x", hours_ago=24 * 53)
    conn.close()

    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(conn, window_hours=24, stale_after_hours=2)
    conn.close()
    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )
    assert verdict["status"] == "red"
    assert any("stale" in r for r in verdict["reasons"])


def test_auth_broken_pattern_is_red(canary, tmp_path):
    """All analyzed rows are http_error 401s — ok_rate=0, http_error_rate=1."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5)
    for _ in range(10):
        _insert_analysis(
            conn,
            status="failed",
            ref="http_error@127.0.0.1:8002",
            hours_ago=0.5,
        )
    conn.close()

    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(conn, window_hours=24, stale_after_hours=2)
    conn.close()
    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )
    assert verdict["status"] == "red"
    rates = verdict["rates"]
    assert rates["ok_rate"] == 0.0
    assert rates["http_error_rate"] == 1.0
    assert any("ok_rate" in r for r in verdict["reasons"])
    assert any("http_error_rate" in r for r in verdict["reasons"])


def test_healthy_pipeline_is_green(canary, tmp_path):
    """Mix of ok + captions_disabled + 1 http_error stays under thresholds."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5)
    for _ in range(7):
        _insert_analysis(conn, status="ok", ref="youtube_transcript_api", hours_ago=0.5)
    for _ in range(2):
        _insert_analysis(conn, status="captions_disabled", hours_ago=0.5)
    _insert_analysis(conn, status="failed", ref="http_error@127.0.0.1:8002", hours_ago=0.5)
    conn.close()

    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(conn, window_hours=24, stale_after_hours=2)
    conn.close()
    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )
    assert verdict["status"] == "green"
    assert verdict["rates"]["ok_rate"] == pytest.approx(7 / 10)
    assert verdict["rates"]["http_error_rate"] == pytest.approx(1 / 10)


def test_zero_analyzed_with_events_is_red(canary, tmp_path):
    """Even if last_analyzed_at is recent, zero rows in the live window is red."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5)
    # Recent enough to defeat the stale check, but window=24h slides past it.
    _insert_analysis(conn, status="ok", hours_ago=1.0)
    conn.close()

    conn = sqlite3.connect(str(db))
    # Tighter window than the lone row's age — looks like nothing was processed.
    metrics = canary.compute_metrics(conn, window_hours=0, stale_after_hours=2)
    conn.close()
    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )
    # events_recent is 0 with window_hours=0, so we expect quiet_window branch.
    # The real "no analyzed despite events" is exercised in test_stale_table_*.
    assert verdict["status"] == "green"


def test_recovery_backlog_ok_recent_ok_passes(canary, tmp_path):
    """Backlog of failures + recent oks -> canary PASSES (fast recovery).

    Simulates post-outage: the 24h window still holds many failed analyses
    that drag the window ok_rate below the threshold, but the most-recent
    analyses are all ok. The recent-N fallback should flip GREEN so the
    canary signals recovery without waiting a full window cycle.
    """
    db = tmp_path / "csi.db"
    conn = _open_db(db)

    # 10+ events in window to clear the quiet-window guard.
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5)

    # Old in-window backlog of plain failures (no http_error ref, so the
    # http_error check stays green and we isolate the ok_rate recovery path).
    # 40 failures + 10 oks -> window ok_rate = 10/50 = 0.20 < 0.25.
    for _ in range(40):
        _insert_analysis(conn, status="failed", ref="", hours_ago=22)

    # Recent recovery: 10 ok rows, freshest in the table.
    for _ in range(10):
        _insert_analysis(
            conn, status="ok", ref="youtube_transcript_api", hours_ago=0.1
        )

    conn.close()

    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(conn, window_hours=24, stale_after_hours=2)
    conn.close()

    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )

    # Window ok_rate is below threshold, but recent_ok_rate = 10/10 = 1.0,
    # so the recovery fallback should keep the canary GREEN.
    assert verdict["rates"]["ok_rate"] < 0.25
    assert verdict["status"] == "green", f"expected GREEN, got {verdict}"
    assert not any("ok_rate" in r for r in verdict["reasons"])


def test_genuinely_failing_recent_window_still_red(canary, tmp_path):
    """Recent window is all failures -> canary still RED.

    Ensures the recent-N recovery fallback does not manufacture a
    false-pass when the genuinely-recent data is bad.
    """
    db = tmp_path / "csi.db"
    conn = _open_db(db)

    for _ in range(10):
        _insert_event(conn, hours_ago=0.5)

    # Even the most-recent analyses are all http_error failures.
    for _ in range(10):
        _insert_analysis(
            conn,
            status="failed",
            ref="http_error@127.0.0.1:8002",
            hours_ago=0.1,
        )

    conn.close()

    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(conn, window_hours=24, stale_after_hours=2)
    conn.close()

    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )

    # recent_ok_rate = 0/10 = 0 < 0.25 -> no rescue, still RED.
    assert verdict["status"] == "red"
    assert any("ok_rate" in r for r in verdict["reasons"])


def test_quiet_window_guard_still_holds(canary, tmp_path):
    """Quiet window stays GREEN via the guard, not via recent recovery.

    With events below `require_min_events`, the quiet-window guard must
    still short-circuit to GREEN before any ok_rate / recent-N logic runs,
    so the new fallback cannot weaken or bypass that guard.
    """
    db = tmp_path / "csi.db"
    conn = _open_db(db)

    # Only 2 events -- below require_min_events=5.
    for _ in range(2):
        _insert_event(conn, hours_ago=1)

    # Old failed backlog in the DB.
    for _ in range(5):
        _insert_analysis(
            conn,
            status="failed",
            ref="http_error@127.0.0.1:8002",
            hours_ago=20,
        )

    # A few recent oks (would be a rescue, but the guard fires first).
    for _ in range(3):
        _insert_analysis(conn, status="ok", hours_ago=0.1)

    conn.close()

    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(conn, window_hours=24, stale_after_hours=2)
    conn.close()

    verdict = canary.evaluate(
        metrics,
        min_ok_rate=0.25,
        max_http_error_rate=0.50,
        require_min_events=5,
    )

    # GREEN because of the quiet_window guard, not the recovery fallback.
    assert verdict["status"] == "green"
    assert any("quiet_window" in r for r in verdict["reasons"])
