"""Verify csi_bridge.csi_recent_reports surfaces hackernews_movers_signal events (P2.B3).

The csi_recent_reports tool reads from the CSI events table and filters
by an explicit event_type whitelist. P2.B3 adds `hackernews_movers_signal`
to that whitelist so HN events emitted by Lane B (P2.B1/P2.B2) flow into
the csi-trend-analyst agent and other consumers.

This test seeds a tmp csi.db with one HN event + one non-whitelisted event
+ one already-whitelisted event, calls the wrapper, and asserts:
  - the HN event is in the result
  - the non-whitelisted event is NOT
  - the existing-whitelist behavior (rss_trend_report) is preserved
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from universal_agent.tools import csi_bridge


def _seed_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                dedupe_key TEXT NOT NULL,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                emitted_at TEXT,
                subject_json TEXT NOT NULL,
                routing_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                delivered INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX idx_events_dedupe ON events(dedupe_key);
        """)
        # Insert three events: one HN, one whitelisted-already, one not-whitelisted.
        rows = [
            ("hn:1001:20260509", "dk-hn", "hackernews", "hackernews_movers_signal",
             "2026-05-09T18:00:00+00:00", "2026-05-09T18:00:00+00:00",
             json.dumps({"story_id": 1001, "title": "Test HN story"}),
             json.dumps({"lane": "hackernews"}), json.dumps({})),
            ("rss:101:20260509", "dk-rss", "rss", "rss_trend_report",
             "2026-05-09T17:00:00+00:00", "2026-05-09T17:00:00+00:00",
             json.dumps({"report_type": "rss_trend_report"}),
             json.dumps({}), json.dumps({})),
            ("noise:1:20260509", "dk-noise", "other", "some_other_unwhitelisted_type",
             "2026-05-09T16:00:00+00:00", "2026-05-09T16:00:00+00:00",
             json.dumps({}), json.dumps({}), json.dumps({})),
        ]
        conn.executemany(
            "INSERT INTO events (event_id, dedupe_key, source, event_type, "
            "occurred_at, received_at, subject_json, routing_json, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def tmp_csi_db(tmp_path: Path, monkeypatch) -> Path:
    db = tmp_path / "csi.db"
    _seed_db(db)
    monkeypatch.setenv("CSI_DB_PATH", str(db))
    return db


def _call_wrapper(args: dict) -> dict:
    """Run the async wrapper and unwrap the JSON response.

    csi_recent_reports_wrapper is decorated with @tool from
    claude_agent_sdk, so we call its `.handler` attribute (the
    underlying async function) rather than the SdkMcpTool object.
    """
    handler = csi_bridge.csi_recent_reports_wrapper.handler
    result = asyncio.run(handler(args))
    text = result["content"][0]["text"]
    if text.startswith("error:"):
        return {"_error": text}
    return json.loads(text)


def test_hackernews_movers_signal_in_whitelisted_event_types(tmp_csi_db: Path) -> None:
    """P2.B3: hackernews_movers_signal events must surface from csi_recent_reports."""
    payload = _call_wrapper({"limit": 50, "include_artifacts": False})
    assert payload.get("status") == "ok"
    types = {r["event_type"] for r in payload.get("reports", [])}
    assert "hackernews_movers_signal" in types, (
        f"hackernews_movers_signal missing from result; types found: {types}"
    )


def test_existing_rss_whitelist_still_works(tmp_csi_db: Path) -> None:
    """Sanity: the existing rss_trend_report whitelist isn't broken by the addition."""
    payload = _call_wrapper({"limit": 50, "include_artifacts": False})
    types = {r["event_type"] for r in payload.get("reports", [])}
    assert "rss_trend_report" in types


def test_unwhitelisted_types_are_excluded(tmp_csi_db: Path) -> None:
    """Sanity: events with unknown event_types are still filtered out."""
    payload = _call_wrapper({"limit": 50, "include_artifacts": False})
    types = {r["event_type"] for r in payload.get("reports", [])}
    assert "some_other_unwhitelisted_type" not in types
