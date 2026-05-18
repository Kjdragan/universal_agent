"""Integration test for the youtube_transcript_coverage invariant.

Seeds a real sqlite DB with the same schema rss_event_analysis uses in
production, registers the invariant via module import, and runs the watchdog
end-to-end.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Iterable

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rss_event_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL DEFAULT 'youtube_channel_rss',
    transcript_status TEXT NOT NULL DEFAULT 'missing',
    analyzed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


@pytest.fixture(autouse=True)
def _register_youtube_invariant():
    """Each test starts with only the YouTube invariant registered."""
    clear_registry_for_tests()
    # Importing the submodule registers the invariant.  We reload to ensure
    # the decorator runs against the freshly cleared registry.
    import importlib

    from universal_agent.services.invariants import youtube_invariants

    importlib.reload(youtube_invariants)
    yield
    clear_registry_for_tests()


def _seed_db(db_path: Path, rows: Iterable[tuple[str, str, str]]) -> None:
    """rows: iterable of (event_id, source, transcript_status) inserted at now()."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        for event_id, source, status in rows:
            conn.execute(
                "INSERT INTO rss_event_analysis (event_id, source, transcript_status) "
                "VALUES (?, ?, ?)",
                (event_id, source, status),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_events(db_path: Path, rows: Iterable[tuple[str, str]]) -> None:
    """rows: iterable of (event_id, source) inserted at now()."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        for event_id, source in rows:
            conn.execute(
                "INSERT INTO events (event_id, source, occurred_at) "
                "VALUES (?, ?, datetime('now'))",
                (event_id, source),
            )
        conn.commit()
    finally:
        conn.close()


def _only_finding_with_id(findings, metric_key):
    return [f for f in findings if f.metric_key == metric_key]


def test_invariant_is_registered_after_module_import() -> None:
    ids = [inv.id for inv in pi.get_registered_invariants()]
    assert "youtube_transcript_coverage" in ids
    assert "youtube_enrichment_coverage" in ids


def test_all_missing_emits_critical_finding(tmp_path: Path) -> None:
    db = tmp_path / "csi.db"
    _seed_db(
        db,
        [(f"e{i}", "youtube_channel_rss", "missing") for i in range(20)],
    )

    findings = run_invariants({"csi_db_path": db})
    transcript_findings = _only_finding_with_id(findings, "youtube_transcript_coverage")
    assert len(transcript_findings) == 1
    f = transcript_findings[0]
    assert f.finding_id == "invariant:youtube_transcript_coverage"
    assert f.severity == "critical"
    assert f.category == "proactive_health"
    obs = f.observed_value
    assert isinstance(obs, dict)
    assert obs["days_inspected"] >= 1
    assert len(obs["offending_days"]) >= 1
    day = obs["offending_days"][0]
    assert day["ok_count"] == 0
    assert day["ok_pct"] == 0.0


def test_all_ok_emits_no_finding(tmp_path: Path) -> None:
    db = tmp_path / "csi.db"
    _seed_db(
        db,
        [(f"e{i}", "youtube_channel_rss", "ok") for i in range(20)],
    )

    findings = run_invariants({"csi_db_path": db})
    assert findings == []


def test_mixed_with_majority_ok_emits_no_finding(tmp_path: Path) -> None:
    # 9 ok / 1 missing → 90% ok ≥ 50% floor → no finding.
    db = tmp_path / "csi.db"
    rows = [(f"ok{i}", "youtube_channel_rss", "ok") for i in range(9)]
    rows.append(("miss1", "youtube_channel_rss", "missing"))
    _seed_db(db, rows)

    findings = run_invariants({"csi_db_path": db})
    assert findings == []


def test_below_min_rows_per_day_is_skipped(tmp_path: Path) -> None:
    # Only 2 rows — under MIN_ROWS_PER_DAY=3 — even all missing should not fire.
    db = tmp_path / "csi.db"
    _seed_db(
        db,
        [("e1", "youtube_channel_rss", "missing"), ("e2", "youtube_channel_rss", "missing")],
    )

    findings = run_invariants({"csi_db_path": db})
    assert findings == []


def test_missing_db_path_returns_no_finding() -> None:
    findings = run_invariants({"csi_db_path": None})
    assert findings == []


def test_nonexistent_db_path_returns_no_finding(tmp_path: Path) -> None:
    findings = run_invariants({"csi_db_path": tmp_path / "does_not_exist.db"})
    assert findings == []


def test_other_source_rows_are_ignored(tmp_path: Path) -> None:
    db = tmp_path / "csi.db"
    # 10 rows from a different source, all missing → should NOT trigger,
    # because the invariant only looks at youtube_channel_rss.
    _seed_db(
        db,
        [(f"e{i}", "discord_rss", "missing") for i in range(10)],
    )

    findings = run_invariants({"csi_db_path": db})
    assert findings == []


def test_table_missing_emits_probe_error(tmp_path: Path) -> None:
    # DB file exists but has no rss_event_analysis table → sqlite.Error
    # → transcript_coverage runner converts to probe_error finding (warn).
    # enrichment_coverage swallows the same error silently (treats as N/A)
    # because the events table is also absent on a fresh box.
    db = tmp_path / "csi.db"
    sqlite3.connect(str(db)).close()  # create empty DB

    findings = run_invariants({"csi_db_path": db})
    transcript_findings = _only_finding_with_id(
        findings, "youtube_transcript_coverage_probe_error"
    )
    assert len(transcript_findings) == 1
    f = transcript_findings[0]
    assert f.finding_id.endswith(":probe_error")
    assert f.severity == "warn"
    assert "rss_event_analysis" in (f.observed_value or "")
    # enrichment_coverage should NOT emit a probe_error (it catches sqlite.Error).
    enrichment_probe_errors = _only_finding_with_id(
        findings, "youtube_enrichment_coverage_probe_error"
    )
    assert enrichment_probe_errors == []


# === youtube_enrichment_coverage tests ===


def test_enrichment_coverage_fires_when_events_have_no_enrichment(tmp_path: Path) -> None:
    """The exact original 38/38 failure: events exist, rss_event_analysis empty."""
    db = tmp_path / "csi.db"
    # 10 events but zero rss_event_analysis rows.
    _seed_events(db, [(f"e{i}", "youtube_channel_rss") for i in range(10)])

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert len(enrichment_findings) == 1
    f = enrichment_findings[0]
    assert f.finding_id == "invariant:youtube_enrichment_coverage"
    assert f.severity == "critical"
    obs = f.observed_value
    assert obs["total_events"] == 10
    assert obs["enriched_events"] == 0
    assert obs["coverage_pct"] == 0.0


def test_enrichment_coverage_quiet_when_above_floor(tmp_path: Path) -> None:
    db = tmp_path / "csi.db"
    _seed_events(db, [(f"e{i}", "youtube_channel_rss") for i in range(10)])
    # Add matching rss_event_analysis rows for 8/10 events (80% coverage).
    conn = sqlite3.connect(str(db))
    try:
        for i in range(8):
            conn.execute(
                "INSERT INTO rss_event_analysis (event_id, source, transcript_status) "
                "VALUES (?, 'youtube_channel_rss', 'ok')",
                (f"e{i}",),
            )
        conn.commit()
    finally:
        conn.close()

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert enrichment_findings == []


def test_enrichment_coverage_quiet_when_below_min_events(tmp_path: Path) -> None:
    """A handful of events with no enrichment should not fire the alarm —
    too small a sample to be confident."""
    db = tmp_path / "csi.db"
    _seed_events(db, [(f"e{i}", "youtube_channel_rss") for i in range(3)])

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert enrichment_findings == []


def test_enrichment_coverage_quiet_when_csi_db_missing() -> None:
    findings = run_invariants({"csi_db_path": None})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert enrichment_findings == []


def test_enrichment_coverage_ignores_other_sources(tmp_path: Path) -> None:
    db = tmp_path / "csi.db"
    _seed_events(db, [(f"e{i}", "reddit_discovery") for i in range(10)])

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert enrichment_findings == []
