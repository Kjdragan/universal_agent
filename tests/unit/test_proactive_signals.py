from __future__ import annotations

import json
import sqlite3

from universal_agent import task_hub
from universal_agent.proactive_signals import (
    apply_card_action,
    generate_youtube_cards,
    list_cards,
    record_feedback,
    sync_generated_cards,
    upsert_generated_card,
)
from universal_agent.services.proactive_artifacts import list_artifacts
from universal_agent.services.proactive_convergence import get_topic_signature


def _seed_csi_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            subject_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE rss_event_analysis (
            event_id TEXT UNIQUE NOT NULL,
            transcript_status TEXT,
            transcript_chars INTEGER,
            category TEXT,
            summary_text TEXT,
            analysis_json TEXT,
            analyzed_at TEXT
        )
        """
    )
    subjects = [
        {
            "event_id": "yt-1",
            "subject": {
                "video_id": "video_one_1",
                "url": "https://www.youtube.com/watch?v=video_one_1",
                "title": "Claude Code agent harness deep dive",
                "description": "Detailed workflow for agentic coding harnesses.",
                "channel_name": "Small Educator",
            },
            "analysis": {
                "transcript_status": "ok",
                "transcript_chars": 12000,
                "category": "ai_coding",
                "summary_text": "Transcript-backed walkthrough of an agent harness.",
                "analysis_json": {"themes": ["claude code", "agentic coding", "harness"]},
            },
        },
        {
            "event_id": "yt-2",
            "subject": {
                "video_id": "video_two_2",
                "url": "https://www.youtube.com/watch?v=video_two_2",
                "title": "Claude Code workflow notes",
                "description": "A practical agentic coding workflow.",
                "channel_name": "Another Channel",
            },
            "analysis": None,
        },
        {
            "event_id": "yt-short",
            "subject": {
                "video_id": "short_video",
                "url": "https://www.youtube.com/shorts/short_video",
                "title": "Claude Code short",
                "description": "Should be filtered.",
                "channel_name": "Shorts Channel",
            },
            "analysis": None,
        },
    ]
    for item in subjects:
        conn.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, subject_json) VALUES (?, 'youtube_channel_rss', 'channel_new_upload', '2026-04-13T00:00:00Z', ?)",
            (item["event_id"], json.dumps(item["subject"])),
        )
        if item["analysis"]:
            analysis = item["analysis"]
            conn.execute(
                "INSERT INTO rss_event_analysis (event_id, transcript_status, transcript_chars, category, summary_text, analysis_json, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, '2026-04-13 00:05:00')",
                (
                    item["event_id"],
                    analysis["transcript_status"],
                    analysis["transcript_chars"],
                    analysis["category"],
                    analysis["summary_text"],
                    json.dumps(analysis["analysis_json"]),
                ),
            )
    conn.commit()
    conn.close()


def test_youtube_signal_cards_prioritize_transcript_and_filter_shorts(tmp_path):
    csi_db = tmp_path / "csi.db"
    _seed_csi_db(csi_db)

    cards = generate_youtube_cards(csi_db)

    assert cards
    joined = "\n".join(card["summary"] + " " + json.dumps(card["evidence"]) for card in cards)
    assert "short_video" not in joined
    transcript_cards = [card for card in cards if "Transcript" in card["summary"] or card["card_type"] == "transcript_insight"]
    metadata_cards = [card for card in cards if card["card_type"] == "diamond"]
    assert transcript_cards
    assert metadata_cards
    assert max(card["confidence_score"] for card in transcript_cards) > max(card["confidence_score"] for card in metadata_cards)


def test_signal_feedback_and_action_create_task(tmp_path):
    db = tmp_path / "activity.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    card = upsert_generated_card(
        conn,
        {
            "card_id": "youtube-video:test",
            "source": "youtube",
            "card_type": "diamond",
            "title": "YouTube candidate: agent harness",
            "summary": "Metadata says this is worth exploring.",
            "actions": [{"id": "fetch_transcripts", "label": "Fetch Transcript", "description": "Fetch the transcript."}],
            "evidence": [{"title": "agent harness", "url": "https://www.youtube.com/watch?v=abc"}],
        },
    )

    updated = record_feedback(
        conn,
        card_id=card["card_id"],
        tags=["more_like_this", "novel"],
        text="This is the right shape.",
        actor="tester",
    )
    assert updated["feedback"]["tag_counts"]["novel"] == 1

    actioned = apply_card_action(
        conn,
        card_id=card["card_id"],
        action_id="fetch_transcripts",
        actor="tester",
    )
    task_id = actioned["selected_action"]["task_id"]
    task = task_hub.get_item(conn, task_id)
    assert task is not None
    assert task["source_kind"] == "proactive_signal"

    cards = list_cards(conn)
    assert cards[0]["status"] == "actioned"


def test_sync_generated_cards_creates_artifacts_signatures_and_tutorial_tasks(tmp_path):
    csi_db = tmp_path / "csi.db"
    _seed_csi_db(csi_db)
    db = tmp_path / "activity.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)

    counts = sync_generated_cards(conn, csi_db_path=csi_db)
    repeated = sync_generated_cards(conn, csi_db_path=csi_db)
    signature = get_topic_signature(conn, "video_one_1")
    artifacts = list_artifacts(conn, limit=100)
    tutorial_tasks = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items WHERE source_kind = 'tutorial_build'"
    ).fetchone()["c"]

    assert counts["youtube"] >= 1
    assert counts["topic_signatures"] >= 1
    # Second sync is idempotent — no new signatures upserted
    assert repeated["topic_signatures"] == 0
    assert signature is not None
    assert tutorial_tasks >= 1
    assert any(item["source_kind"] == "proactive_signal" for item in artifacts)
