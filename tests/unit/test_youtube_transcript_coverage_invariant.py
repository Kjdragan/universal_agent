"""Integration tests for the youtube_* pipeline invariants.

Seeds a real sqlite DB with the same schema rss_event_analysis / events use in
production, registers the invariants via module import, and runs the watchdog
end-to-end.  Covers both ``youtube_transcript_coverage`` and the domain-aware
``youtube_enrichment_coverage`` (which mirrors the selective enricher's
eligibility — see csi_rss_semantic_enrich.py).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sqlite3
from typing import Iterable

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.invariants import youtube_invariants
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)

# Production-faithful columns the domain-aware enrichment invariant relies on:
# events.delivered / events.subject_json (channel_name) and
# rss_event_analysis.channel_name / category (skip-set computation).
SCHEMA = """
CREATE TABLE IF NOT EXISTS rss_event_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL DEFAULT 'youtube_channel_rss',
    channel_name TEXT,
    category TEXT,
    transcript_status TEXT NOT NULL DEFAULT 'missing',
    analyzed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    subject_json TEXT,
    delivered INTEGER NOT NULL DEFAULT 0,
    occurred_at TEXT NOT NULL
);
"""


@pytest.fixture(autouse=True)
def _register_youtube_invariant():
    """Each test starts with only the YouTube invariants registered."""
    clear_registry_for_tests()
    # Importing the submodule registers the invariant.  We reload to ensure
    # the decorator runs against the freshly cleared registry.
    import importlib

    importlib.reload(youtube_invariants)
    yield
    clear_registry_for_tests()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    return conn


def _seed_db(db_path: Path, rows: Iterable[tuple[str, str, str]]) -> None:
    """rows: iterable of (event_id, source, transcript_status) inserted at now()."""
    conn = _connect(db_path)
    try:
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
    """rows: iterable of (event_id, source) inserted at now(), delivered=1.

    subject_json is ``'{}'`` (no channel_name) — prod-reachable (the column is
    NOT NULL in production) and exercises the COALESCE(...,'') channel path.
    """
    conn = _connect(db_path)
    try:
        for event_id, source in rows:
            conn.execute(
                "INSERT INTO events (event_id, source, subject_json, delivered, occurred_at) "
                "VALUES (?, ?, '{}', 1, datetime('now'))",
                (event_id, source),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_event(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    source: str = "youtube_channel_rss",
    channel_name: str | None = None,
    delivered: int = 1,
    enriched: bool = False,
    category: str | None = None,
    transcript_status: str = "ok",
    age_days: int = 0,
) -> None:
    """Insert one event (and optionally its matching rss_event_analysis row).

    subject_json is always valid JSON (prod's column is NOT NULL); a None
    channel_name becomes ``{"channel_name": null}`` so json_extract yields NULL,
    exercising the COALESCE(...,'') path.  age_days lets a test place events
    outside the WINDOW_DAYS window.
    """
    subject = json.dumps({"channel_name": channel_name})
    conn.execute(
        "INSERT INTO events (event_id, source, subject_json, delivered, occurred_at) "
        "VALUES (?, ?, ?, ?, datetime('now', ?))",
        (event_id, source, subject, delivered, f"-{age_days} days"),
    )
    if enriched:
        conn.execute(
            "INSERT INTO rss_event_analysis "
            "(event_id, source, channel_name, category, transcript_status) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, source, channel_name, category, transcript_status),
        )


def _seed_analysis_history(
    conn: sqlite3.Connection,
    channel_name: str,
    category: str,
    count: int,
    *,
    prefix: str = "hist",
) -> None:
    """Seed analysis-only history rows so a channel crosses the skip threshold.

    These have no matching ``events`` row, so they only influence the skip-set
    computation (``_nondomain_skip_names``), not the coverage ratio.
    """
    for i in range(count):
        conn.execute(
            "INSERT INTO rss_event_analysis "
            "(event_id, source, channel_name, category, transcript_status) "
            "VALUES (?, 'youtube_channel_rss', ?, ?, 'ok')",
            (f"{prefix}-{channel_name}-{i}", channel_name, category),
        )


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


def _seed_db_days(
    db_path: Path, days: Iterable[tuple[int, int, int]], *, prefix: str = "d"
) -> None:
    """Seed per-day analysis rows: iterable of (age_days, ok_count, missing_count)."""
    conn = _connect(db_path)
    try:
        for age_days, ok_count, missing_count in days:
            for i in range(ok_count):
                conn.execute(
                    "INSERT INTO rss_event_analysis "
                    "(event_id, source, transcript_status, analyzed_at) "
                    "VALUES (?, 'youtube_channel_rss', 'ok', datetime('now', ?))",
                    (f"{prefix}{age_days}-ok{i}", f"-{age_days} days"),
                )
            for i in range(missing_count):
                conn.execute(
                    "INSERT INTO rss_event_analysis "
                    "(event_id, source, transcript_status, analyzed_at) "
                    "VALUES (?, 'youtube_channel_rss', 'missing', datetime('now', ?))",
                    (f"{prefix}{age_days}-miss{i}", f"-{age_days} days"),
                )
        conn.commit()
    finally:
        conn.close()


def test_recovered_pipeline_downgrades_residue_to_warn(tmp_path: Path) -> None:
    """Recovered-guard: when the 2 most recent populated days are BOTH at/
    above the floor, the offending older days are residue aging out of the
    7-day window — the finding downgrades from critical (pages via the
    email digest) to warn (dashboard-only) and says so. Context: the
    pipeline recovered 2026-06-07 but the Jun 5–7 residue kept paging
    4 emails/day until it left the window."""
    db = tmp_path / "csi.db"
    _seed_db_days(
        db,
        [
            (0, 5, 0),   # today: healthy
            (1, 5, 0),   # yesterday: healthy
            (3, 0, 5),   # residue: broken day aging out
        ],
    )
    findings = run_invariants({"csi_db_path": db})
    matches = _only_finding_with_id(findings, "youtube_transcript_coverage")
    assert len(matches) == 1
    f = matches[0]
    assert f.severity == "warn"
    assert "RECOVERED" in (f.recommendation or "")
    assert f.metadata.get("recovered") is True


def test_not_recovered_when_most_recent_day_below_floor(tmp_path: Path) -> None:
    """Still broken today → stays critical, no downgrade."""
    db = tmp_path / "csi.db"
    _seed_db_days(
        db,
        [
            (0, 0, 5),   # today: broken
            (1, 5, 0),   # yesterday: healthy
        ],
    )
    findings = run_invariants({"csi_db_path": db})
    matches = _only_finding_with_id(findings, "youtube_transcript_coverage")
    assert len(matches) == 1
    f = matches[0]
    assert f.severity == "critical"
    assert f.metadata.get("recovered") is False


def test_single_healthy_day_after_incident_stays_critical(tmp_path: Path) -> None:
    """One healthy day isn't recovery — both of the 2 most recent populated
    days must clear the floor before downgrading."""
    db = tmp_path / "csi.db"
    _seed_db_days(
        db,
        [
            (0, 5, 0),   # today: healthy
            (1, 0, 5),   # yesterday: still broken
        ],
    )
    findings = run_invariants({"csi_db_path": db})
    matches = _only_finding_with_id(findings, "youtube_transcript_coverage")
    assert len(matches) == 1
    assert matches[0].severity == "critical"


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


# === youtube_enrichment_coverage tests (domain-aware, PR #660 selective enricher) ===


def test_enrichment_coverage_fires_when_events_have_no_enrichment(tmp_path: Path) -> None:
    """The exact original 38/38 failure: in-scope events exist, analysis empty."""
    db = tmp_path / "csi.db"
    # 10 delivered events, zero rss_event_analysis rows → no skip set → all in-scope.
    _seed_events(db, [(f"e{i}", "youtube_channel_rss") for i in range(10)])

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert len(enrichment_findings) == 1
    f = enrichment_findings[0]
    assert f.finding_id == "invariant:youtube_enrichment_coverage"
    assert f.severity == "critical"
    obs = f.observed_value
    assert obs["in_scope_events"] == 10
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
    """A handful of in-scope events with no enrichment should not fire — too
    small a sample to be confident."""
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
    _seed_events(db, [(f"e{i}", "discord_rss") for i in range(10)])

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert enrichment_findings == []


def test_selective_skip_of_nondomain_channel_does_not_trip_floor(tmp_path: Path) -> None:
    """The core regression: a non-domain channel with a large un-enriched backlog
    must NOT drag coverage below the floor, because the enricher intentionally
    skips it.  A flat events-vs-analysis check would report ~31% and fire."""
    db = tmp_path / "csi.db"
    conn = _connect(db)
    try:
        # Domain channel: 6 delivered events, all enriched (ai_coding) → in-scope, 100%.
        for i in range(6):
            _seed_event(
                conn,
                f"ai-{i}",
                channel_name="AICoder Daily",
                enriched=True,
                category="ai_coding",
            )
        # Non-domain channel: history makes it majority-non-domain (skip set),
        # plus a big un-enriched backlog that a flat check would count.
        _seed_analysis_history(conn, "Preppy Kitchen", "cooking", 4)
        for i in range(20):
            _seed_event(conn, f"cook-{i}", channel_name="Preppy Kitchen", enriched=False)
        conn.commit()
    finally:
        conn.close()

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    # In-scope = the 6 AICoder events (100% enriched). Preppy Kitchen is skipped.
    assert enrichment_findings == []


def test_enrichment_outage_on_domain_channel_still_fires(tmp_path: Path) -> None:
    """Even with non-domain channels skipped, a real outage on a DOMAIN channel
    (the events the enricher owns) must still surface as CRITICAL."""
    db = tmp_path / "csi.db"
    conn = _connect(db)
    try:
        # Non-domain channel skipped (history), with backlog — must be excluded.
        _seed_analysis_history(conn, "Preppy Kitchen", "cooking", 4)
        for i in range(20):
            _seed_event(conn, f"cook-{i}", channel_name="Preppy Kitchen", enriched=False)
        # Domain channel with 10 delivered events, NONE enriched → real outage.
        for i in range(10):
            _seed_event(conn, f"ai-{i}", channel_name="AICoder Daily", enriched=False)
        conn.commit()
    finally:
        conn.close()

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert len(enrichment_findings) == 1
    obs = enrichment_findings[0].observed_value
    assert obs["in_scope_events"] == 10  # only the domain channel
    assert obs["enriched_events"] == 0
    assert obs["coverage_pct"] == 0.0
    assert obs["skipped_nondomain_channels"] >= 1


def test_undelivered_events_are_excluded_from_scope(tmp_path: Path) -> None:
    """delivered=0 events are never processed by the enricher, so they must not
    count against coverage."""
    db = tmp_path / "csi.db"
    conn = _connect(db)
    try:
        # 6 delivered + enriched domain events → in-scope, 100%.
        for i in range(6):
            _seed_event(
                conn, f"ai-{i}", channel_name="AICoder Daily", enriched=True, category="ai_coding"
            )
        # 20 UNDELIVERED, un-enriched domain events → must be out of scope.
        for i in range(20):
            _seed_event(conn, f"pending-{i}", channel_name="AICoder Daily", delivered=0)
        conn.commit()
    finally:
        conn.close()

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert enrichment_findings == []


def test_always_keep_channel_remains_in_scope(tmp_path: Path, monkeypatch) -> None:
    """A channel on the always-keep allowlist is NOT skipped even with a
    majority-non-domain history, so the enricher is held to coverage on it."""
    monkeypatch.setenv("CSI_RSS_SELECTION_ALWAYS_KEEP", "Jake Broe")
    db = tmp_path / "csi.db"
    conn = _connect(db)
    try:
        # Jake Broe: majority-non-domain history (geopolitics) but always-keep →
        # in-scope. 3 enriched + 10 un-enriched delivered events → 23% → fires.
        _seed_analysis_history(conn, "Jake Broe", "geopolitics", 3, prefix="jbhist")
        for i in range(3):
            _seed_event(
                conn, f"jb-ok-{i}", channel_name="Jake Broe", enriched=True, category="geopolitics"
            )
        for i in range(10):
            _seed_event(conn, f"jb-miss-{i}", channel_name="Jake Broe", enriched=False)
        conn.commit()
    finally:
        conn.close()

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert len(enrichment_findings) == 1
    obs = enrichment_findings[0].observed_value
    # Jake Broe stays in scope (13 in-scope events), so its low coverage fires.
    assert obs["in_scope_events"] == 13
    assert obs["skipped_nondomain_channels"] == 0


def test_events_outside_window_are_excluded(tmp_path: Path) -> None:
    """Pins the WINDOW_DAYS bound. 8 recent in-domain events are fully enriched
    (100%); 10 old (30d) in-domain events are un-enriched. If the window worked,
    coverage = 8/8 = healthy → quiet. If a regression widened/dropped the window,
    the old events would be counted (8/18 = 44%) and it would fire."""
    db = tmp_path / "csi.db"
    conn = _connect(db)
    try:
        for i in range(8):
            _seed_event(
                conn, f"recent-{i}", channel_name="AICoder Daily", enriched=True, category="ai_coding"
            )
        for i in range(10):
            _seed_event(
                conn, f"old-{i}", channel_name="AICoder Daily", enriched=False, age_days=30
            )
        conn.commit()
    finally:
        conn.close()

    findings = run_invariants({"csi_db_path": db})
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert enrichment_findings == []


def test_null_channel_name_handled_in_skip_and_scope(tmp_path: Path) -> None:
    """NULL/absent channel_name must (a) not break the skip-set sorted() and
    (b) stay in scope (it can't match the NOT IN skip list). With a non-empty
    skip set active, 10 delivered un-enriched NULL-channel events fire at 0%."""
    db = tmp_path / "csi.db"
    conn = _connect(db)
    try:
        # Named non-domain channel → enters the skip set.
        _seed_analysis_history(conn, "Preppy Kitchen", "cooking", 4)
        # NULL-channel non-domain analysis history — would raise TypeError in
        # sorted({None, 'Preppy Kitchen'}) without the in-loop guard.
        for i in range(3):
            conn.execute(
                "INSERT INTO rss_event_analysis "
                "(event_id, source, channel_name, category, transcript_status) "
                "VALUES (?, 'youtube_channel_rss', NULL, 'cooking', 'ok')",
                (f"nullhist-{i}",),
            )
        # 10 delivered, un-enriched, NULL-channel events → in scope (NOT skip-listed).
        for i in range(10):
            _seed_event(conn, f"nochan-{i}", channel_name=None, enriched=False)
        conn.commit()
    finally:
        conn.close()

    findings = run_invariants({"csi_db_path": db})
    # The guard prevents a TypeError → no probe_error surfaces.
    assert _only_finding_with_id(findings, "youtube_enrichment_coverage_probe_error") == []
    enrichment_findings = _only_finding_with_id(findings, "youtube_enrichment_coverage")
    assert len(enrichment_findings) == 1
    obs = enrichment_findings[0].observed_value
    assert obs["in_scope_events"] == 10
    assert obs["coverage_pct"] == 0.0
    assert obs["skipped_nondomain_channels"] >= 1  # Preppy Kitchen (None excluded by guard)


# === drift guard: invariant eligibility mirror must match the enricher source ===


def _parse_enricher_constant(name: str):
    """Statically extract a constant's value from the enricher source (no import).

    The enricher lives in a separate package/venv (CSI_Ingester) and imports
    csi_ingester.* — importing it here is neither safe nor available, so we parse
    the literal with ``ast``.
    """
    enricher = (
        Path(__file__).resolve().parents[2]
        / "CSI_Ingester"
        / "development"
        / "scripts"
        / "csi_rss_semantic_enrich.py"
    )
    if not enricher.exists():
        pytest.skip(f"enricher source not present in this checkout: {enricher}")
    tree = ast.parse(enricher.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return ast.literal_eval(node.value)
    pytest.fail(f"{name} not found in enricher source {enricher}")


def test_domain_cats_in_sync_with_enricher() -> None:
    """The invariant mirrors the enricher's _DOMAIN_CATS as a single source of
    truth. If they drift, coverage scoping diverges from real eligibility — fail
    loudly so the two are edited together (e.g. when adding geopolitics)."""
    enricher_cats = set(_parse_enricher_constant("_DOMAIN_CATS"))
    assert enricher_cats == youtube_invariants._DOMAIN_CATS, (
        "youtube_invariants._DOMAIN_CATS drifted from csi_rss_semantic_enrich.py::_DOMAIN_CATS. "
        "Edit both together so coverage scoping matches enricher eligibility."
    )


def test_always_keep_default_in_sync_with_enricher() -> None:
    """The always-keep default must also mirror the enricher so the invariant
    scopes the same operator-pinned channels."""
    enricher_default = _parse_enricher_constant("_DEFAULT_ALWAYS_KEEP")
    assert enricher_default == youtube_invariants._DEFAULT_ALWAYS_KEEP
