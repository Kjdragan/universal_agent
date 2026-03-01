from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


script_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(script_dir))
import csi_reddit_telegram_digest
import csi_rss_telegram_digest


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


def _insert_event(conn: sqlite3.Connection, *, source: str, subject: dict) -> None:
    conn.execute(
        """
        INSERT INTO events (
            event_id, dedupe_key, source, event_type, occurred_at, received_at,
            subject_json, routing_json, metadata_json, delivered, created_at
        ) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), ?, '{}', '{}', 1, datetime('now'))
        """,
        (
            f"evt_{source}_1",
            f"dk_{source}_1",
            source,
            "channel_new_upload" if source == "youtube_channel_rss" else "subreddit_new_post",
            json.dumps(subject),
        ),
    )
    conn.commit()


def test_reddit_digest_resets_cursor_when_ahead(tmp_path: Path, monkeypatch, capsys):
    db_path = tmp_path / "csi.db"
    state_path = tmp_path / "reddit_state.json"

    conn = sqlite3.connect(str(db_path))
    _create_events_table(conn)
    _insert_event(
        conn,
        source="reddit_discovery",
        subject={
            "subreddit": "artificial",
            "title": "hello",
            "permalink": "https://reddit.com/r/artificial/1",
            "score": 1,
            "num_comments": 0,
        },
    )
    conn.close()

    state_path.write_text(json.dumps({"last_sent_id": 500}), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "csi_reddit_telegram_digest.py",
            "--db-path",
            str(db_path),
            "--state-path",
            str(state_path),
            "--chat-id",
            "dummy",
            "--bot-token",
            "dummy",
            "--dry-run",
        ],
    )

    rc = csi_reddit_telegram_digest.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "REDDIT_TELEGRAM_CURSOR_AHEAD" in out
    assert "REDDIT_TELEGRAM_NEW_COUNT=1" in out


def test_rss_digest_resets_cursor_when_ahead(tmp_path: Path, monkeypatch, capsys):
    db_path = tmp_path / "csi.db"
    state_path = tmp_path / "rss_state.json"

    conn = sqlite3.connect(str(db_path))
    _create_events_table(conn)
    conn.execute(
        """
        CREATE TABLE rss_event_analysis (
            event_id TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL DEFAULT 'other_interest',
            analysis_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    _insert_event(
        conn,
        source="youtube_channel_rss",
        subject={
            "channel_id": "UC_TEST",
            "channel_name": "Test Creator",
            "video_id": "vid123",
            "title": "new upload",
            "url": "https://youtube.com/watch?v=vid123",
        },
    )
    conn.close()

    state_path.write_text(json.dumps({"last_sent_id": 1795}), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "csi_rss_telegram_digest.py",
            "--db-path",
            str(db_path),
            "--state-path",
            str(state_path),
            "--chat-id",
            "dummy",
            "--bot-token",
            "dummy",
            "--dry-run",
        ],
    )

    rc = csi_rss_telegram_digest.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "RSS_TELEGRAM_CURSOR_AHEAD" in out
    assert "RSS_TELEGRAM_NEW_COUNT=1" in out
