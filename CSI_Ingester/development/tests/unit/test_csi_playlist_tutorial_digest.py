from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import sys

script_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(script_dir))
import csi_playlist_tutorial_digest
from csi_playlist_tutorial_digest import _find_stalled_workspace_turns, _prune_pending_by_age


def _create_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
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
        )
        """
    )
    conn.commit()


def _insert_event(conn: sqlite3.Connection, *, source: str, event_type: str = "video_added_to_playlist") -> None:
    conn.execute(
        """
        INSERT INTO events (
            event_id, dedupe_key, source, event_type, occurred_at, received_at,
            subject_json, routing_json, metadata_json, delivered, created_at
        ) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), '{}', '{}', '{}', 1, datetime('now'))
        """,
        (f"evt_{source}_1", f"dk_{source}_1", source, event_type),
    )
    conn.commit()


def test_find_stalled_workspace_turns_ignores_old_turns(tmp_path: Path):
    """
    Test that if a session directory has multiple turn files, only the absolutely newest
    turn file is evaluated. An older, unfinalized turn should not flag the session as stalled 
    if the newer turn is finalized.
    """
    video_id = "test_vid_123"
    session_dir = tmp_path / f"session_hook_yt_abc123_{video_id}"
    turns_dir = session_dir / "turns"
    turns_dir.mkdir(parents=True)
    
    old_turn = turns_dir / "turn_old.jsonl"
    new_turn = turns_dir / "turn_new.jsonl"
    
    # Write an old turn that started but NEVER finalized
    old_turn.write_text(json.dumps({"event": "turn_started", "timestamp": "2026-02-01T00:00:00Z"}) + "\n", encoding="utf-8")
    
    # Write a new turn that started AND finalized
    new_turn.write_text(
        json.dumps({"event": "turn_started", "timestamp": "2026-02-28T00:00:00Z"}) + "\n" +
        json.dumps({"event": "turn_finalized", "timestamp": "2026-02-28T00:05:00Z"}) + "\n",
        encoding="utf-8"
    )
    
    # Force modification times so old_turn is older
    base_time = time.time() - 100000
    os.utime(old_turn, (base_time, base_time))
    os.utime(new_turn, (time.time(), time.time()))
    
    now = datetime(2026, 2, 28, 1, 0, 0, tzinfo=timezone.utc)
    
    stalled = _find_stalled_workspace_turns(
        workspace_root=tmp_path,
        video_id=video_id,
        min_age_minutes=15,
        now=now,
    )
    
    # Because the newest turn (turn_new) is finalized, the session is NOT stalled.
    assert stalled == []


def test_find_stalled_workspace_turns_flags_new_stalled_turn(tmp_path: Path):
    """
    Test that if the absolutely newest turn is unfinalized and past the min_age_minutes, 
    it correctly flags the session as stalled.
    """
    video_id = "test_vid_123"
    session_dir = tmp_path / f"session_hook_yt_abc123_{video_id}"
    turns_dir = session_dir / "turns"
    turns_dir.mkdir(parents=True)
    
    stalled_turn = turns_dir / "turn_stalled.jsonl"
    
    # Write a turn that started 30 mins ago but never finalized
    started_at = "2026-02-28T00:00:00Z"
    stalled_turn.write_text(json.dumps({"event": "turn_started", "timestamp": started_at}) + "\n", encoding="utf-8")
    
    now = datetime(2026, 2, 28, 0, 30, 0, tzinfo=timezone.utc)
    
    stalled = _find_stalled_workspace_turns(
        workspace_root=tmp_path,
        video_id=video_id,
        min_age_minutes=15,
        now=now,
    )
    
    assert len(stalled) == 1
    assert stalled[0]["turn_id"] == "turn_stalled"
    assert stalled[0]["started_at"] == started_at
    assert stalled[0]["age_minutes"] == 30


def test_prune_pending_by_age_drops_only_stale_items():
    now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    pending = {
        "fresh_vid": {
            "video_id": "fresh_vid",
            "pending_since": "2026-03-01T10:30:00Z",
            "created_at": "2026-03-01T10:30:00Z",
        },
        "stale_vid": {
            "video_id": "stale_vid",
            "pending_since": "2026-02-20T10:30:00Z",
            "created_at": "2026-02-20T10:30:00Z",
        },
    }

    kept, dropped = _prune_pending_by_age(pending, max_age_hours=48, now=now)

    assert dropped == 1
    assert "fresh_vid" in kept
    assert "stale_vid" not in kept


def test_prune_pending_by_age_can_be_disabled():
    now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    pending = {
        "very_old": {
            "video_id": "very_old",
            "pending_since": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
        }
    }

    kept, dropped = _prune_pending_by_age(pending, max_age_hours=0, now=now)

    assert dropped == 0
    assert "very_old" in kept


def test_playlist_digest_defaults_to_playlist_source_only(tmp_path: Path, monkeypatch, capsys):
    db_path = tmp_path / "csi.db"
    state_path = tmp_path / "state.json"
    conn = sqlite3.connect(str(db_path))
    _create_events_table(conn)
    _insert_event(conn, source="youtube_channel_rss", event_type="channel_new_upload")
    conn.close()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "csi_playlist_tutorial_digest.py",
            "--db-path",
            str(db_path),
            "--state-path",
            str(state_path),
            "--dry-run",
        ],
    )
    rc = csi_playlist_tutorial_digest.main()
    out = capsys.readouterr().out
    assert rc == 0
    # Default source must ignore creator RSS rows.
    assert "PLAYLIST_TUTORIAL_NEW_COUNT=0" in out


def test_playlist_digest_can_read_non_default_source_only_when_explicit(tmp_path: Path, monkeypatch, capsys):
    db_path = tmp_path / "csi.db"
    state_path = tmp_path / "state.json"
    conn = sqlite3.connect(str(db_path))
    _create_events_table(conn)
    _insert_event(conn, source="youtube_channel_rss", event_type="channel_new_upload")
    conn.close()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "csi_playlist_tutorial_digest.py",
            "--db-path",
            str(db_path),
            "--state-path",
            str(state_path),
            "--source",
            "youtube_channel_rss",
            "--dry-run",
        ],
    )
    rc = csi_playlist_tutorial_digest.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "PLAYLIST_TUTORIAL_NEW_COUNT=1" in out
