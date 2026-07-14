"""Unit tests for the YouTube transcript pipeline freshness canary.

The canary is backlog-aware: it goes RED on a *real* stall (aging unprocessed
work) and stays GREEN on stale-freshness-with-no-work (a quiet-for-domain
window). Covered:
  1. EMIT-stall — events pile up un-emitted (delivered=0) — the 2026-07-14 wedge.
  2. ENRICH-stall — emitted, eligible events aging without analysis.
  3. STALE-BUT-QUIET — old last_analyzed but no aging backlog -> GREEN (no false alarm).
  4. AUTH-broken — fresh rows exist but ok-rate suppressed / http-errors high.
  5. quiet-window + happy-path branches.
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
            emitted_at TEXT,
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


def _insert_event(
    conn,
    *,
    hours_ago: float = 0,
    delivered: int = 0,
    emitted_hours_ago: float | None = None,
) -> str:
    """Insert a youtube event. When delivered=1 it also gets an ``emitted_at``
    (defaults to ``hours_ago``) — that is the timestamp the enrich-stall age is
    measured from."""
    eid = f"evt_{uuid.uuid4().hex[:10]}"
    if delivered:
        emit = hours_ago if emitted_hours_ago is None else emitted_hours_ago
        conn.execute(
            "INSERT INTO events (event_id, source, delivered, created_at, emitted_at) "
            "VALUES (?, 'youtube_channel_rss', 1, datetime('now', ?), datetime('now', ?))",
            (eid, f"-{hours_ago} hours", f"-{emit} hours"),
        )
    else:
        conn.execute(
            "INSERT INTO events (event_id, source, delivered, created_at) "
            "VALUES (?, 'youtube_channel_rss', 0, datetime('now', ?))",
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


def _metrics_and_verdict(canary, db, *, window_hours=24, stale_after_hours=2, **kw):
    conn = sqlite3.connect(str(db))
    metrics = canary.compute_metrics(
        conn, window_hours=window_hours, stale_after_hours=stale_after_hours
    )
    conn.close()
    verdict = canary.evaluate(
        metrics,
        min_ok_rate=kw.get("min_ok_rate", 0.25),
        max_http_error_rate=kw.get("max_http_error_rate", 0.50),
        require_min_events=kw.get("require_min_events", 5),
        emit_stale_hours=kw.get("emit_stale_hours", 6.0),
    )
    return metrics, verdict


def test_quiet_window_returns_green(canary, tmp_path):
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(2):
        _insert_event(conn, hours_ago=1)
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db)
    assert verdict["status"] == "green"
    assert any("quiet_window" in r for r in verdict["reasons"])


def test_emit_stalled_backlog_is_red(canary, tmp_path):
    """Events pile up un-emitted (delivered=0) past the emit threshold — the
    exact 2026-07-14 batch_brief wedge. Must be RED with an emit_stalled reason."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=8, delivered=0)  # 8h > 6h emit threshold
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db)
    assert verdict["status"] == "red"
    assert any("emit_stalled" in r for r in verdict["reasons"])


def test_enrich_stalled_eligible_backlog_is_red(canary, tmp_path):
    """Emitted (delivered=1), eligible events aging without analysis -> RED."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=12, delivered=1)  # emitted, never analyzed
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db, stale_after_hours=2)
    assert verdict["status"] == "red"
    assert any("enrich_stalled" in r for r in verdict["reasons"])


def test_stale_freshness_but_no_backlog_is_green(canary, tmp_path):
    """THE anti-false-alarm case: last_analyzed is old (past the stale
    threshold) but there is NO aging backlog — everything emitted was analyzed
    and nothing is stuck. The old canary screamed here; the new one stays GREEN.
    """
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    # 10 events, all emitted AND analyzed 20h ago; nothing un-emitted or eligible.
    for _ in range(10):
        eid = _insert_event(conn, hours_ago=20, delivered=1)
        _insert_analysis(conn, event_id=eid, status="ok", hours_ago=20)
    conn.close()
    metrics, verdict = _metrics_and_verdict(canary, db, stale_after_hours=10)
    # Freshness IS stale (would have been RED under the old age-only rule)...
    assert metrics["last_analyzed_age_hours"] > 10
    # ...but there is no aging work, so the smart canary stays GREEN.
    assert verdict["status"] == "green", f"false alarm: {verdict}"


def test_auth_broken_pattern_is_red(canary, tmp_path):
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5)
    for _ in range(10):
        _insert_analysis(conn, status="failed", ref="http_error@127.0.0.1:8002", hours_ago=0.5)
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db)
    assert verdict["status"] == "red"
    rates = verdict["rates"]
    assert rates["ok_rate"] == 0.0
    assert rates["http_error_rate"] == 1.0
    assert any("ok_rate" in r for r in verdict["reasons"])
    assert any("http_error_rate" in r for r in verdict["reasons"])


def test_healthy_pipeline_is_green(canary, tmp_path):
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5, delivered=1)
    for _ in range(7):
        _insert_analysis(conn, status="ok", ref="youtube_transcript_api", hours_ago=0.5)
    for _ in range(2):
        _insert_analysis(conn, status="captions_disabled", hours_ago=0.5)
    _insert_analysis(conn, status="failed", ref="http_error@127.0.0.1:8002", hours_ago=0.5)
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db)
    assert verdict["status"] == "green"
    assert verdict["rates"]["ok_rate"] == pytest.approx(7 / 10)
    assert verdict["rates"]["http_error_rate"] == pytest.approx(1 / 10)


def test_recovery_backlog_ok_recent_ok_passes(canary, tmp_path):
    """Post-outage: window ok_rate is dragged down by an old failure backlog,
    but the most-recent analyses are all ok -> canary PASSES (fast recovery)."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5, delivered=1)
    for _ in range(40):
        _insert_analysis(conn, status="failed", ref="", hours_ago=22)
    for _ in range(10):
        _insert_analysis(conn, status="ok", ref="youtube_transcript_api", hours_ago=0.1)
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db)
    assert verdict["rates"]["ok_rate"] < 0.25
    assert verdict["status"] == "green", f"expected GREEN, got {verdict}"
    assert not any("ok_rate" in r for r in verdict["reasons"])


def test_genuinely_failing_recent_window_still_red(canary, tmp_path):
    """Recent window is all failures -> still RED (recovery fallback can't rescue)."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(10):
        _insert_event(conn, hours_ago=0.5, delivered=1)
    for _ in range(10):
        _insert_analysis(conn, status="failed", ref="http_error@127.0.0.1:8002", hours_ago=0.1)
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db)
    assert verdict["status"] == "red"
    assert any("ok_rate" in r for r in verdict["reasons"])


def test_quiet_window_guard_still_holds(canary, tmp_path):
    """Below require_min_events, the quiet-window guard short-circuits to GREEN."""
    db = tmp_path / "csi.db"
    conn = _open_db(db)
    for _ in range(2):
        _insert_event(conn, hours_ago=1)
    for _ in range(5):
        _insert_analysis(conn, status="failed", ref="http_error@127.0.0.1:8002", hours_ago=20)
    conn.close()
    _, verdict = _metrics_and_verdict(canary, db)
    assert verdict["status"] == "green"
    assert any("quiet_window" in r for r in verdict["reasons"])
