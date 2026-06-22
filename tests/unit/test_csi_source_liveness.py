"""Tests for the universal CSI source liveness invariant.

Background: prior to P1a, only `youtube_channel_rss` had invariant coverage
(via the two YouTube-specific invariants in PR #367). Other CSI
adapters were entirely invisible to the watchdog. Live data on 2026-05-20
showed several of them dead (40h+ no events) with zero alerts. P1a adds one
invariant that covers all monitored adapters by checking max(occurred_at)
per source vs. an expected-cadence threshold.

The experimental threads_* lanes (threads_owned, threads_trends_seeded,
threads_trends_broad) were decommissioned 2026-06-22 (no live ingestion
adapter, X-API-dependent, redundant with the @ClaudeDevs/@bcherny lane), so
youtube_channel_rss is now the only continuously-live CSI source; hackernews
is parked behind UA_HACKERNEWS_SNAPSHOT_ENABLED.
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


def test_multiple_stale_sources_listed_in_one_finding(tmp_path: Path, monkeypatch) -> None:
    """Multiple monitored sources dead → ONE finding listing them all (operator
    gets the full picture per alert, not one email per source). One source is
    seeded fresh so we can assert the finding contains only the stale set.
    Built from the effective (parking-aware) monitored set so it stays correct
    as lanes are added/removed. UA_HACKERNEWS_SNAPSHOT_ENABLED is armed here so
    hackernews joins the effective set — without it, only youtube_channel_rss
    would be monitored (the threads_* lanes were decommissioned 2026-06-22) and
    the multi-source premise wouldn't hold."""
    monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", "1")
    from universal_agent.services.invariants.csi_source_liveness import (
        effective_source_thresholds,
    )
    db = tmp_path / "csi.db"
    monitored = list(effective_source_thresholds())
    assert len(monitored) >= 2  # youtube_channel_rss + armed hackernews
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


def test_threads_lanes_decommissioned_not_monitored(tmp_path: Path, monkeypatch) -> None:
    """The experimental threads_* lanes were decommissioned 2026-06-22 (no live
    ingestion adapter, X-API-dependent, redundant with the @ClaudeDevs lane).
    They must NOT be in the threshold table or the effective set, and stale
    threads_* rows in the DB must NOT produce a finding — even if the old
    UA_CSI_THREADS_LANES_ENABLED flag is set (it is now inert). Mirrors the
    csi_analytics/#990 + youtube_playlist/#438 retirement precedent."""
    # The old gating flag is gone; setting it must NOT resurrect the lanes.
    monkeypatch.setenv("UA_CSI_THREADS_LANES_ENABLED", "1")
    from universal_agent.services.invariants import csi_source_liveness as mod
    from universal_agent.services.invariants.csi_source_liveness import (
        SOURCE_THRESHOLDS_HOURS,
        effective_source_thresholds,
    )
    threads_lanes = {"threads_owned", "threads_trends_seeded", "threads_trends_broad"}
    # No threads source is monitored, the gating helper is gone, and the
    # threshold table no longer carries them.
    assert not (threads_lanes & set(SOURCE_THRESHOLDS_HOURS))
    assert not (threads_lanes & set(effective_source_thresholds()))
    assert not hasattr(mod, "_THREADS_SOURCES")
    assert not hasattr(mod, "_threads_lanes_enabled")

    db = tmp_path / "csi.db"
    # Every still-monitored source is fresh; stale threads_* rows must be ignored.
    rows = [(s, _hours_ago(0.5)) for s in effective_source_thresholds()]
    rows += [(s, _hours_ago(200)) for s in threads_lanes]  # would be stale if monitored
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_hackernews_parked_when_snapshot_flag_off(
    tmp_path: Path, monkeypatch
) -> None:
    """HN is parked behind UA_HACKERNEWS_SNAPSHOT_ENABLED — the SAME flag that
    arms its only producer (the hackernews_snapshot cron). With the flag unset or
    off, HN is excluded from the effective monitored set. This reverts the
    2026-06-06 un-park: grep (2026-06-21) found POST /api/v1/hackernews/refresh
    has ZERO internal callers (only the manual dashboard refresh button), so the
    "overnight convergence /refresh producer" premise was false — live csi.db
    shows 0 HN events/48h (a source with no producer)."""
    from universal_agent.services.invariants.csi_source_liveness import (
        _HACKERNEWS_SOURCES,
        effective_source_thresholds,
    )
    for flag in (None, "0", "off", "false"):
        if flag is None:
            monkeypatch.delenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", raising=False)
        else:
            monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", flag)
        assert "hackernews" not in effective_source_thresholds()
        assert not (_HACKERNEWS_SOURCES & set(effective_source_thresholds()))


def test_hackernews_parked_emits_no_finding_even_when_db_is_ancient(
    tmp_path: Path, monkeypatch
) -> None:
    """The whole point of the re-park: with the snapshot cron off, an ancient or
    absent HN events table must NOT produce a finding. Before this revert the
    probe false-flagged HN every heartbeat (last real HN event was ~127h stale)."""
    monkeypatch.delenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", raising=False)
    from universal_agent.services.invariants.csi_source_liveness import (
        effective_source_thresholds,
    )
    db = tmp_path / "csi.db"
    rows = [
        (s, _hours_ago(0.5)) for s in effective_source_thresholds() if s != "hackernews"
    ]
    rows.append(("hackernews", _hours_ago(400)))
    _seed_events(db, rows)
    findings = run_invariants({"csi_db_path": db})
    matches = [f for f in findings if f.metric_key == "csi_source_liveness"]
    assert matches == []


def test_hackernews_bursty_gap_within_widened_threshold_does_not_fire(
    tmp_path: Path, monkeypatch
) -> None:
    """HN is only monitored while UA_HACKERNEWS_SNAPSHOT_ENABLED arms the snapshot
    cron (its sole producer). With the cron ON, a multi-hour daytime gap is still
    NORMAL bursty HN cadence; the widened threshold must not fire at e.g. 10h
    silence — the old 3h threshold false-flagged exactly this, which (via the
    disproven /refresh-producer premise) drove the 2026-06-06 un-park."""
    monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", "1")  # arm the cron so HN is monitored
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


def test_hackernews_monitored_when_snapshot_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    """Flipping UA_HACKERNEWS_SNAPSHOT_ENABLED=1 re-activates HN monitoring (the
    snapshot cron is now armed), so a dead HN lane fires again — mirroring the
    Threads re-arm test. This is also the 'fires when truly dead' case under the
    corrected flag-gated model: the old test gated on flag=0, which now parks HN;
    the flag-on path is the one that actually monitors."""
    monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", "1")
    from universal_agent.services.invariants.csi_source_liveness import (
        _HACKERNEWS_SOURCES,
        SOURCE_THRESHOLDS_HOURS,
        effective_source_thresholds,
    )
    assert _HACKERNEWS_SOURCES <= set(effective_source_thresholds())
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
    """Locks the data-driven threshold while the snapshot cron is ON (HN monitored).
    30d of live csi.db (2026-06-10) showed a legitimate-but-quiet 94h gap
    (06-06->06-09; max; next-largest 27.6h). The old 36h threshold false-flagged
    that spell, which made the proactive_health digest re-spam on every
    critical/clear flip. The threshold must sit comfortably above the observed max
    so a normal bursty quiet spell never fires."""
    monkeypatch.setenv("UA_HACKERNEWS_SNAPSHOT_ENABLED", "1")  # arm the cron so HN is monitored
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
