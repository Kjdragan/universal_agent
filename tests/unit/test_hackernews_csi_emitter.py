"""Unit tests for the Hacker News CSI emitter (Lane B, P2.B1).

The emitter writes `hackernews_movers_signal` events into the CSI
events bus when the snapshot's `movers` block contains "material"
front-page activity (new debuts, big climbs, high-score drops, top-3
controversial). 24h dedup via the `dedupe_keys` companion table.

Tests use an in-memory sqlite (or a tmp_path file) to verify schema
correctness, materiality filter, and dedup behavior — no production
csi.db is touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from universal_agent.services import hackernews_csi_emitter as emitter

# ─── fixtures ──────────────────────────────────────────────────────────


def _seed_schema(db_path: Path) -> None:
    """Create the events + dedupe_keys tables matching production schema."""
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
            CREATE TABLE dedupe_keys (
                key TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _make_snapshot(
    *,
    added: list[str] | None = None,
    moved: list[dict[str, Any]] | None = None,
    removed: list[str] | None = None,
    controversial: list[dict[str, Any]] | None = None,
    movers_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a normalized snapshot fixture with a `movers.changes` array (post-Phase-1)."""
    if movers_changes is None:
        movers_changes = []
        for sid in (added or []):
            movers_changes.append({"id": sid, "title": f"#{sid}", "status": "new",
                                    "rank": 0, "score": 100, "delta": 0})
        for m in (moved or []):
            movers_changes.append({"id": m.get("id"), "title": m.get("title", "?"),
                                    "status": "moved", "rank": m.get("to_rank", 0),
                                    "score": m.get("score", 0),
                                    "delta": m.get("delta", 0)})
        for sid in (removed or []):
            movers_changes.append({"id": sid, "title": f"dropped {sid}",
                                    "status": "dropped", "rank": 0,
                                    "score": 250, "delta": 0})

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 2,
            "watchlist": ["claude", "agent"],
            "errors": [],
            "duration_seconds": 5.0,
        },
        "top_stories": [],
        "controversial": controversial or [],
        "movers": {
            "since": "2026-05-09T00:00:00Z",
            "changes": movers_changes,
        },
        "pulses": {},
        "show_hn": [],
        "ask_hn": [],
        "hiring": {"companies": []},
    }


@pytest.fixture
def csi_db(tmp_path: Path) -> Path:
    db = tmp_path / "csi.db"
    _seed_schema(db)
    return db


# ─── DB-missing safety ─────────────────────────────────────────────────


def test_emit_skips_when_db_missing(tmp_path: Path) -> None:
    snap = _make_snapshot(added=["1001"])
    nonexistent = tmp_path / "missing.db"
    emitted = emitter.emit_movers_signals(snap, csi_db_path=nonexistent)
    assert emitted == 0


def test_emit_skips_when_db_missing_required_table(tmp_path: Path) -> None:
    """If the schema is missing or unrecognizable, emit returns 0 without raising."""
    db = tmp_path / "broken.db"
    sqlite3.connect(str(db)).close()  # empty file, no tables
    snap = _make_snapshot(added=["1001"])
    emitted = emitter.emit_movers_signals(snap, csi_db_path=db)
    assert emitted == 0


# ─── materiality filter ────────────────────────────────────────────────


def test_emit_includes_status_new_always(csi_db: Path) -> None:
    snap = _make_snapshot(added=["1001", "1002"])
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 2


def test_emit_includes_high_delta_moves(csi_db: Path) -> None:
    """|delta| >= 3 is material; smaller moves are filtered."""
    snap = _make_snapshot(moved=[
        {"id": "2001", "delta": 5, "score": 100, "to_rank": 2},   # YES
        {"id": "2002", "delta": -3, "score": 100, "to_rank": 5},  # YES (abs=3)
        {"id": "2003", "delta": 1, "score": 100, "to_rank": 7},   # NO
        {"id": "2004", "delta": 2, "score": 100, "to_rank": 9},   # NO
    ])
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 2


def test_emit_includes_high_score_drops_only(csi_db: Path) -> None:
    """status=dropped emits only when score>=200; low-score drops are noise."""
    movers_changes = [
        {"id": "3001", "title": "high-score drop", "status": "dropped",
         "rank": 0, "score": 250, "delta": 0},  # YES
        {"id": "3002", "title": "low-score drop", "status": "dropped",
         "rank": 0, "score": 50, "delta": 0},   # NO
    ]
    snap = _make_snapshot(movers_changes=movers_changes)
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 1


def test_emit_includes_top_3_controversial(csi_db: Path) -> None:
    """The first 3 entries from snapshot.controversial each get one signal."""
    snap = _make_snapshot(controversial=[
        {"id": "4001", "title": "ctv 1", "score": 200, "descendants": 800},
        {"id": "4002", "title": "ctv 2", "score": 100, "descendants": 500},
        {"id": "4003", "title": "ctv 3", "score": 80, "descendants": 400},
        {"id": "4004", "title": "ctv 4 (skipped)", "score": 60, "descendants": 300},
    ])
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 3


def test_emit_combines_movers_and_controversial(csi_db: Path) -> None:
    snap = _make_snapshot(
        added=["1001"],
        controversial=[{"id": "4001", "title": "ctv", "score": 100, "descendants": 200}],
    )
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 2  # 1 mover + 1 controversial


def test_emit_returns_zero_when_nothing_material(csi_db: Path) -> None:
    snap = _make_snapshot(moved=[
        {"id": "2003", "delta": 1, "score": 100, "to_rank": 7},  # subthreshold
    ])
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 0


# ─── 24h dedup ─────────────────────────────────────────────────────────


def test_emit_dedupes_within_24h(csi_db: Path) -> None:
    """Re-emitting on the same day must not produce duplicate events."""
    snap = _make_snapshot(added=["1001"])
    first = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    second = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert first == 1
    assert second == 0  # deduped

    conn = sqlite3.connect(str(csi_db))
    try:
        rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert rows == 1
    finally:
        conn.close()


def test_emit_re_emits_next_day(csi_db: Path) -> None:
    """Same story can re-emit on a later day (dedup TTL = 24h, day-bucketed)."""
    snap = _make_snapshot(added=["1001"])
    today = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    tomorrow = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

    n1 = emitter.emit_movers_signals(snap, csi_db_path=csi_db, now=today)
    n2 = emitter.emit_movers_signals(snap, csi_db_path=csi_db, now=tomorrow)

    assert n1 == 1
    assert n2 == 1  # re-emit on next day's bucket

    conn = sqlite3.connect(str(csi_db))
    try:
        rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert rows == 2
    finally:
        conn.close()


# ─── event payload shape ───────────────────────────────────────────────


def test_emit_writes_correct_subject_json(csi_db: Path) -> None:
    snap = _make_snapshot(added=["1001"])
    emitter.emit_movers_signals(snap, csi_db_path=csi_db)

    conn = sqlite3.connect(str(csi_db))
    try:
        row = conn.execute("SELECT subject_json, source, event_type, dedupe_key FROM events").fetchone()
    finally:
        conn.close()

    subject = json.loads(row[0])
    assert subject["story_id"] in ("1001", 1001)
    assert subject["movement"]["status"] == "new"
    assert "comment_url" in subject
    assert row[1] == "hackernews"
    assert row[2] == "hackernews_movers_signal"
    assert row[3].startswith("hn:1001:")


def test_emit_populates_topic_match_when_title_contains_watchlist(csi_db: Path) -> None:
    """topic_match[] should list watchlist topics whose substring appears in the title."""
    movers_changes = [{
        "id": "1001",
        "title": "New AGENT framework for Claude integrations",
        "status": "new",
        "rank": 0, "score": 100, "delta": 0,
    }]
    snap = _make_snapshot(movers_changes=movers_changes)
    snap["meta"]["watchlist"] = ["claude", "agent", "codex"]
    emitter.emit_movers_signals(snap, csi_db_path=csi_db)

    conn = sqlite3.connect(str(csi_db))
    try:
        subject = json.loads(conn.execute("SELECT subject_json FROM events").fetchone()[0])
    finally:
        conn.close()
    assert set(subject.get("topic_match", [])) == {"claude", "agent"}  # case-insensitive


def test_emit_writes_routing_and_metadata(csi_db: Path) -> None:
    snap = _make_snapshot(added=["1001"])
    emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    conn = sqlite3.connect(str(csi_db))
    try:
        row = conn.execute("SELECT routing_json, metadata_json FROM events").fetchone()
    finally:
        conn.close()
    routing = json.loads(row[0])
    metadata = json.loads(row[1])
    assert routing.get("lane") == "hackernews"
    assert routing.get("category") == "movers"
    assert "snapshot_generated_at" in metadata


# ─── edge cases ────────────────────────────────────────────────────────


def test_emit_handles_missing_movers_block(csi_db: Path) -> None:
    snap = _make_snapshot()
    snap["movers"] = None
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 0  # gracefully nothing


def test_emit_handles_no_changes_array(csi_db: Path) -> None:
    snap = _make_snapshot()
    snap["movers"] = {"since": "x"}  # no `changes`
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 0


def test_emit_handles_non_dict_snapshot(csi_db: Path) -> None:
    emitted = emitter.emit_movers_signals(None, csi_db_path=csi_db)  # type: ignore[arg-type]
    assert emitted == 0


def test_emit_continues_on_invalid_change_entry(csi_db: Path) -> None:
    """One garbage entry should not abort the rest."""
    movers_changes = [
        "this is not a dict",
        {"id": "1001", "title": "valid new", "status": "new",
         "rank": 0, "score": 100, "delta": 0},
    ]
    snap = _make_snapshot(movers_changes=movers_changes)
    emitted = emitter.emit_movers_signals(snap, csi_db_path=csi_db)
    assert emitted == 1


# ─── public API ────────────────────────────────────────────────────────


@pytest.mark.parametrize("public_name", [
    "emit_movers_signals",
    "EVENT_TYPE",
    "EVENT_SOURCE",
])
def test_public_api(public_name: str) -> None:
    assert hasattr(emitter, public_name), f"hackernews_csi_emitter must export {public_name}"


def test_event_type_is_canonical_string() -> None:
    assert emitter.EVENT_TYPE == "hackernews_movers_signal"


def test_event_source_is_canonical_string() -> None:
    assert emitter.EVENT_SOURCE == "hackernews"
