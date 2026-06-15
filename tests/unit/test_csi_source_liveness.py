"""Tests for the universal CSI source liveness invariant.

Background: prior to P1a, only `youtube_channel_rss` had invariant coverage
(via the two YouTube-specific invariants in PR #367). Other CSI
adapters — youtube_playlist, threads_owned, threads_trends_seeded,
threads_trends_broad — were entirely invisible to the watchdog. Live data
on 2026-05-20 showed several of them dead (40h+ no events) with zero
alerts. P1a adds one invariant that covers all monitored adapters by
checking max(occurred_at) per source vs. an expected-cadence threshold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sqlite3

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)

UTC = timezone.utc

EVENTS_SCHEMA = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    dedupe_key TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'item',
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT '',
    emitted_at TEXT,
    subject_json TEXT NOT NULL DEFAULT '{}',
    routing_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    delivered INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _fresh_registry():
    """Each test loads only csi_source_liveness so other invariants don't
    cross-contaminate findings counts."""
    clear_registry_for_tests()
    from universal_agent.services.invariants import csi_source_liveness
    importlib.reload(csi_source_liveness)
    yield
    clear_registry_for_tests()


def _seed_events(db_path: Path, rows: list[tuple[str, str]]) -> None:
    """Seed (source, occurred_at_iso) tuples into a fresh csi.db."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(EVENTS_SCHEMA)
        for i, (source, occurred_at) in enumerate(rows):
            conn.execute(
                "INSERT INTO events (event_id, dedupe_key, source, occurred_at, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"e{i}", f"d{i}", source, occurred_at, occurred_at),
            )
        conn.commit()
    finally:
        conn.close()


def _hours_ago(h: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=h)).isoformat()


def test_registers_on_import() -> None:
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert "csi_source_liveness" in ids


def test_all_sources_fresh_emits_nothing(tmp_path: Path) -> None:
    """Every monitored source has an event within its threshold → no finding."""
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
    )
    db = tmp_path / "csi.db"
    # Seed every monitored source with a recent event so nothing trips
    # either the stale OR never_seen branches.
    _seed_events(
        db,
        [(source, _hours_ago(0.5)) for source in SOURCE_THRESHOLDS_HOURS],
    )
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_one_stale_source_emits_one_finding(tmp_path: Path) -> None:
    """Single source past its threshold → one finding listing that source."""
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
    )
    db = tmp_path / "csi.db"
    rows = [(s, _hours_ago(0.5)) for s in SOURCE_THRESHOLDS_HOURS if s != "youtube_channel_rss"]
    rows.append(("youtube_channel_rss", _hours_ago(48)))  # WAY past 12h threshold
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    stale = obs.get("stale_sources") or []
    assert any(s.get("source") == "youtube_channel_rss" for s in stale)
    # Healthy source should NOT appear in stale list
    assert not any(s.get("source") == "hackernews" for s in stale)


def test_multiple_stale_sources_listed_in_one_finding(tmp_path: Path) -> None:
    """Multiple monitored sources dead → ONE finding listing them all (operator
    gets the full picture per alert, not one email per source). One source is
    seeded fresh so we can assert the finding contains only the stale set.
    Built from the effective (parking-aware) monitored set so it stays correct
    as lanes are added/removed."""
    from universal_agent.services.invariants.csi_source_liveness import (
        effective_source_thresholds,
    )
    db = tmp_path / "csi.db"
    monitored = list(effective_source_thresholds())
    fresh = monitored[-1]
    stale_sources_expected = {s for s in monitored if s != fresh}
    rows = [
        (source, _hours_ago(200) if source in stale_sources_expected else _hours_ago(0.5))
        for source in monitored
    ]
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    stale_actual = {s["source"] for s in obs.get("stale_sources") or []}
    assert stale_actual == stale_sources_expected


def test_threads_lanes_parked_by_default(tmp_path: Path, monkeypatch) -> None:
    """Threads lanes are experimental + parked: even with zero threads events
    they must NOT appear as stale while UA_CSI_THREADS_LANES_ENABLED is off."""
    monkeypatch.delenv("UA_CSI_THREADS_LANES_ENABLED", raising=False)
    from universal_agent.services.invariants.csi_source_liveness import (
        _THREADS_SOURCES,
        effective_source_thresholds,
    )
    # No threads source is in the effective set while parked.
    assert not (_THREADS_SOURCES & set(effective_source_thresholds()))
    db = tmp_path / "csi.db"
    # Seed every NON-threads source fresh; threads get nothing.
    _seed_events(db, [(s, _hours_ago(0.5)) for s in effective_source_thresholds()])
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_threads_lanes_monitored_when_flag_enabled(tmp_path: Path, monkeypatch) -> None:
    """Flipping UA_CSI_THREADS_LANES_ENABLED=1 re-activates threads monitoring,
    so a dark threads lane fires again."""
    monkeypatch.setenv("UA_CSI_THREADS_LANES_ENABLED", "1")
    from universal_agent.services.invariants.csi_source_liveness import (
        _THREADS_SOURCES,
        effective_source_thresholds,
    )
    assert _THREADS_SOURCES <= set(effective_source_thresholds())
    db = tmp_path / "csi.db"
    # All non-threads sources fresh; threads_owned never seeded → never_seen.
    rows = [
        (s, _hours_ago(0.5))
        for s in effective_source_thresholds()
        if s != "threads_owned"
    ]
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert len(matches) == 1
    stale = {s["source"] for s in (matches[0].observed_value or {}).get("stale_sources") or []}
    assert "threads_owned" in stale


def test_hackernews_always_monitored_regardless_of_snapshot_flag(
    tmp_path: Path, monkeypatch
) -> None:
    """HN liveness is decoupled from the snapshot-cron flag. PR #757 parked HN
    behind UA_HACKERNEWS_SNAPSHOT_ENABLED on the premise "cron disabled ⟹ source
    dead"; live data (2026-06-06) showed HN stays alive via the convergence
    /refresh path regardless of the cron, so the flag is the wrong key. HN is
    monitored whether the flag is 0 or 1."""
    from universal_agent.services.invariants.csi_source_liveness import (
        effective_source_thresholds,
    )
    for flag in ("0", "1"):
        monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", flag)
        assert "hackernews" in effective_source_thresholds()


def test_hackernews_bursty_gap_within_widened_threshold_does_not_fire(
    tmp_path: Path, monkeypatch
) -> None:
    """HN is bursty (overnight convergence pulls), so a multi-hour daytime gap is
    NORMAL. With the widened threshold it must not fire at e.g. 10h silence — the
    old 3h threshold false-flagged exactly this healthy-but-bursty cadence, which
    is what drove the #757 park."""
    monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", "0")  # flag must not matter
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
        effective_source_thresholds,
    )
    assert SOURCE_THRESHOLDS_HOURS["hackernews"] >= 24.0  # widened from the old 3.0
    db = tmp_path / "csi.db"
    rows = [
        (s, _hours_ago(0.5)) for s in effective_source_thresholds() if s != "hackernews"
    ]
    rows.append(("hackernews", _hours_ago(10)))  # 10h quiet — within widened HN threshold
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_hackernews_fires_when_truly_dead(tmp_path: Path, monkeypatch) -> None:
    """Coverage is restored: if HN produces nothing past its (widened) threshold it
    fires. The #757 park had made a genuine HN outage invisible; un-parking + a
    cadence-appropriate threshold catches a real multi-day silence."""
    monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", "0")
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
        effective_source_thresholds,
    )
    db = tmp_path / "csi.db"
    dead_age = SOURCE_THRESHOLDS_HOURS["hackernews"] + 12.0
    rows = [
        (s, _hours_ago(0.5)) for s in effective_source_thresholds() if s != "hackernews"
    ]
    rows.append(("hackernews", _hours_ago(dead_age)))
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert len(matches) == 1
    stale = {s["source"] for s in (matches[0].observed_value or {}).get("stale_sources") or []}
    assert "hackernews" in stale


def test_hackernews_observed_max_bursty_gap_does_not_fire(
    tmp_path: Path, monkeypatch
) -> None:
    """Locks the data-driven threshold. 30d of live csi.db (2026-06-10) showed a
    legitimate-but-quiet 94h gap (06-06->06-09) while HN stayed alive; the
    next-largest gap was 27.6h. The old 36h threshold false-flagged that 94h
    spell, which made the proactive_health digest re-spam on every critical/clear
    flip. The threshold must sit comfortably above the observed max so a normal
    bursty quiet spell never fires."""
    monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", "0")
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
        effective_source_thresholds,
    )
    # Must clear the observed real-world max gap with margin (not the fragile 2h
    # that 96h would give over a 94h observation).
    assert SOURCE_THRESHOLDS_HOURS["hackernews"] >= 96.0
    db = tmp_path / "csi.db"
    rows = [
        (s, _hours_ago(0.5)) for s in effective_source_thresholds() if s != "hackernews"
    ]
    rows.append(("hackernews", _hours_ago(94.0)))  # the observed legitimate max gap
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_completely_missing_source_emits_finding(tmp_path: Path) -> None:
    """A monitored source with ZERO events in the recent window should also
    fire (never_seen state, distinct from stale). Seed all other sources
    fresh so we isolate just the never_seen case."""
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
    )
    db = tmp_path / "csi.db"
    # All sources fresh EXCEPT youtube_channel_rss (never seeded)
    rows = [
        (source, _hours_ago(0.5))
        for source in SOURCE_THRESHOLDS_HOURS
        if source != "youtube_channel_rss"
    ]
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    stale_list = obs.get("stale_sources") or []
    missing = [s for s in stale_list if s["source"] == "youtube_channel_rss"]
    assert len(missing) == 1
    assert missing[0]["state"] == "never_seen"


def test_csi_analytics_retired_not_monitored(tmp_path: Path) -> None:
    """csi_analytics was retired in PR #990 (the three trend-report timers that
    emitted it were removed). It must NOT appear in the monitored set, and a
    stale csi_analytics row in the DB must NOT produce a finding — mirroring
    the youtube_playlist/#438 precedent."""
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
        effective_source_thresholds,
    )
    assert "csi_analytics" not in SOURCE_THRESHOLDS_HOURS
    assert "csi_analytics" not in effective_source_thresholds()

    db = tmp_path / "csi.db"
    # Every still-monitored source is fresh; a stale csi_analytics row is
    # present but must be ignored (retired sources are not evaluated).
    rows = [(s, _hours_ago(0.5)) for s in effective_source_thresholds()]
    rows.append(("csi_analytics", _hours_ago(200)))  # would be stale if monitored
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_missing_csi_db_path_is_silent(tmp_path: Path) -> None:
    findings = run_invariants({"csi_db_path": None})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_nonexistent_csi_db_path_is_silent(tmp_path: Path) -> None:
    """Path provided but the file doesn't exist (dev box without CSI) →
    silent. Watchdog never crashes on a missing upstream."""
    findings = run_invariants({"csi_db_path": tmp_path / "does_not_exist.db"})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_db_without_events_table_is_silent(tmp_path: Path) -> None:
    """DB file exists but no events table (early dev / migration state) →
    silent. Don't crash the heartbeat over a schema drift."""
    db = tmp_path / "csi.db"
    sqlite3.connect(str(db)).close()  # creates empty DB
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_finding_has_actionable_runbook(tmp_path: Path) -> None:
    """Operator should be able to investigate from the finding alone."""
    db = tmp_path / "csi.db"
    _seed_events(db, [("youtube_channel_rss", _hours_ago(48))])
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert len(matches) == 1
    f = matches[0]
    # runbook_command should mention csi-ingester or events query
    assert f.runbook_command and (
        "csi-ingester" in (f.runbook_command or "")
        or "events" in (f.runbook_command or "")
    )
    # recommendation should name at least one stale source
    assert "youtube_channel_rss" in (f.recommendation or "")
