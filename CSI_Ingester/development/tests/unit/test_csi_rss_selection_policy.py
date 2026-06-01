"""Regression test for the RSS semantic-enrich selection policy.

Guards the 2026-06-01 behavior: gold-first + newest-first + skip channels whose
analysis history is majority non-domain (with an operator-pinned always-keep set),
plus the CSI_RSS_SELECTION_GOLD_FIRST kill switch that restores legacy FIFO.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import uuid

script_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(script_dir))
from csi_ingester.store.sqlite import connect, ensure_schema
import csi_rss_semantic_enrich as enrich


def _insert_event(conn, *, channel_name: str) -> None:
    """Insert a delivered, not-yet-analyzed youtube_channel_rss event.

    Insertion order drives the autoincrement id, so later calls = 'newer'.
    """
    eid = f"evt_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """
        INSERT INTO events (
            event_id, dedupe_key, source, event_type, occurred_at, received_at,
            subject_json, routing_json, metadata_json, delivered
        ) VALUES (?, ?, 'youtube_channel_rss', 'channel_new_upload',
                  datetime('now'), datetime('now'), ?, '{}', '{}', 1)
        """,
        (eid, f"dk_{eid}", json.dumps({"channel_name": channel_name})),
    )


def _insert_history(conn, *, channel_name: str, category: str) -> None:
    conn.execute(
        "INSERT INTO rss_event_analysis (event_id, source, channel_name, category) "
        "VALUES (?, 'youtube_channel_rss', ?, ?)",
        (f"hist_{uuid.uuid4().hex[:10]}", channel_name, category),
    )


def _names(rows) -> list[str]:
    return [json.loads(r["subject_json"])["channel_name"] for r in rows]


def test_selection_policy_gold_first_newest_first_skip_nondomain(tmp_path, monkeypatch):
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"channels": [
        {"channel_name": "AICodeKing", "tier": "gold"},
        {"channel_name": "Random News", "tier": "sidecar"},
    ]}))
    monkeypatch.setenv("CSI_RSS_GOLD_WATCHLIST_PATH", str(wl))
    monkeypatch.setenv("CSI_RSS_SELECTION_ALWAYS_KEEP", "Jake Broe")
    monkeypatch.delenv("CSI_RSS_SELECTION_GOLD_FIRST", raising=False)

    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)

    # Pending (unanalyzed) events, inserted oldest -> newest.
    for ch in ["CookingDaily", "AICodeKing", "Some Dev", "Jake Broe", "Newest Indie"]:
        _insert_event(conn, channel_name=ch)

    # History making CookingDaily + Jake Broe majority non-domain (>=2 analyses).
    for cat in ["cooking", "cooking", "noise"]:
        _insert_history(conn, channel_name="CookingDaily", category=cat)
    for cat in ["geopolitics", "geopolitics"]:
        _insert_history(conn, channel_name="Jake Broe", category=cat)
    conn.commit()

    got = _names(enrich._select_pending(conn, 10))

    assert "CookingDaily" not in got, "majority-non-domain channel should be skipped"
    assert "Jake Broe" in got, "operator-pinned always-keep must not be skipped"
    assert got[0] == "AICodeKing", f"gold channel should sort first, got {got!r}"
    nongold = [c for c in got if c != "AICodeKing"]
    assert nongold == ["Newest Indie", "Jake Broe", "Some Dev"], (
        f"non-gold should be newest-first, got {nongold!r}"
    )


def test_selection_policy_kill_switch_restores_fifo(tmp_path, monkeypatch):
    monkeypatch.setenv("CSI_RSS_SELECTION_GOLD_FIRST", "0")
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)
    for ch in ["First", "Second", "Third"]:
        _insert_event(conn, channel_name=ch)
    conn.commit()

    got = _names(enrich._select_pending(conn, 10))
    assert got == ["First", "Second", "Third"], f"kill switch should be FIFO, got {got!r}"
