from __future__ import annotations

import sqlite3

from csi_ingester.store import opportunity_bundles as store
from csi_ingester.store.sqlite import ensure_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def test_upsert_bundle_inserts_and_decodes_row():
    conn = _conn()
    store.upsert_bundle(
        conn,
        bundle_id="bundle:test:1",
        report_key="opportunity_bundle:test:1",
        window_start_utc="2026-03-01T00:00:00Z",
        window_end_utc="2026-03-01T01:00:00Z",
        confidence_method="heuristic",
        quality_summary={"signal_volume": 12, "coverage_score": 0.81},
        opportunities=[{"opportunity_id": "opp-1", "title": "Momentum theme: agentic ai"}],
        artifact_markdown_path="/tmp/bundle.md",
        artifact_json_path="/tmp/bundle.json",
    )
    row = conn.execute("SELECT * FROM opportunity_bundles WHERE bundle_id = ?", ("bundle:test:1",)).fetchone()
    assert row is not None
    decoded = store.decode_bundle_row(row)
    assert decoded["bundle_id"] == "bundle:test:1"
    assert decoded["report_key"] == "opportunity_bundle:test:1"
    assert decoded["quality_summary"]["signal_volume"] == 12
    assert len(decoded["opportunities"]) == 1
    assert decoded["artifact_paths"]["markdown"] == "/tmp/bundle.md"


def test_upsert_bundle_updates_existing_row():
    conn = _conn()
    store.upsert_bundle(
        conn,
        bundle_id="bundle:test:2",
        report_key="opportunity_bundle:test:2",
        window_start_utc="2026-03-01T00:00:00Z",
        window_end_utc="2026-03-01T01:00:00Z",
        confidence_method="heuristic",
        quality_summary={"signal_volume": 3},
        opportunities=[{"opportunity_id": "opp-old", "title": "Old"}],
        artifact_markdown_path="/tmp/old.md",
        artifact_json_path="/tmp/old.json",
    )
    store.upsert_bundle(
        conn,
        bundle_id="bundle:test:2",
        report_key="opportunity_bundle:test:2",
        window_start_utc="2026-03-01T01:00:00Z",
        window_end_utc="2026-03-01T02:00:00Z",
        confidence_method="heuristic",
        quality_summary={"signal_volume": 9, "coverage_score": 0.92},
        opportunities=[{"opportunity_id": "opp-new", "title": "New"}],
        artifact_markdown_path="/tmp/new.md",
        artifact_json_path="/tmp/new.json",
    )
    row = conn.execute("SELECT * FROM opportunity_bundles WHERE bundle_id = ?", ("bundle:test:2",)).fetchone()
    assert row is not None
    decoded = store.decode_bundle_row(row)
    assert decoded["window_start_utc"] == "2026-03-01T01:00:00Z"
    assert decoded["quality_summary"]["signal_volume"] == 9
    assert decoded["opportunities"][0]["opportunity_id"] == "opp-new"
    assert decoded["artifact_paths"]["json"] == "/tmp/new.json"
