from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import patch

from universal_agent import task_hub
from universal_agent.proactive_signals import (
    CARD_STATUS_DELETED,
    CARD_STATUS_PENDING,
    CARD_STATUS_TRACKING,
    apply_card_action,
    expire_stale_pending_cards,
    generate_signal_cards,
    generate_youtube_cards,
    list_cards,
    purge_aged_terminal_cards,
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
    # Cluster cards were retired 2026-06 — only diamond/transcript_insight remain.
    assert not [c for c in cards if c["card_type"] == "cluster"]
    joined = "\n".join(card["summary"] + " " + json.dumps(card["evidence"]) for card in cards)
    assert "short_video" not in joined
    transcript_cards = [card for card in cards if "Transcript" in card["summary"] or card["card_type"] == "transcript_insight"]
    metadata_cards = [card for card in cards if card["card_type"] == "diamond"]
    assert transcript_cards
    assert metadata_cards
    assert max(card["confidence_score"] for card in transcript_cards) > max(card["confidence_score"] for card in metadata_cards)


def test_expire_stale_pending_cards(tmp_path):
    conn = sqlite3.connect(tmp_path / "activity.db")
    conn.row_factory = sqlite3.Row
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()  # older than the 3-day default
    now = datetime.now(timezone.utc).isoformat()

    def _card(cid, title):
        return {"card_id": cid, "source": "youtube", "card_type": "diamond", "title": title, "summary": "s"}

    upsert_generated_card(conn, _card("stale", "old"))
    conn.execute("UPDATE proactive_signal_cards SET updated_at=? WHERE card_id='stale'", (old,))
    # An OLD video (created_at in the past) that is STILL being refreshed by the
    # tick (fresh updated_at) is KEPT — the TTL keys on updated_at ("still being
    # surfaced"), not created_at (the video's publish time).
    upsert_generated_card(conn, _card("still_current", "old-video-still-in-feed"))
    conn.execute("UPDATE proactive_signal_cards SET created_at=?, updated_at=? WHERE card_id='still_current'", (old, now))
    upsert_generated_card(conn, _card("fresh", "new"))
    upsert_generated_card(conn, _card("kept", "operator-triaged"))
    conn.execute("UPDATE proactive_signal_cards SET status='actioned', updated_at=? WHERE card_id='kept'", (old,))
    conn.commit()

    expired = expire_stale_pending_cards(conn)  # default 3-day updated_at TTL
    assert expired == 1  # only the stale *pending* card (not refreshed in 3+ days)

    def _status(cid):
        return conn.execute("SELECT status FROM proactive_signal_cards WHERE card_id=?", (cid,)).fetchone()[0]

    assert _status("stale") == CARD_STATUS_DELETED        # not refreshed in 5d -> soft-deleted
    assert _status("still_current") == CARD_STATUS_PENDING  # old video but still surfaced -> kept
    assert _status("fresh") == CARD_STATUS_PENDING        # just refreshed -> kept
    assert _status("kept") == "actioned"                  # operator-triaged -> never touched

    # Disabled when TTL <= 0 (escape hatch).
    assert expire_stale_pending_cards(conn, older_than_days=0) == 0


def test_purge_aged_terminal_cards_is_resurface_safe(tmp_path):
    """Hard-purge aged terminal (non-live) cards, but NEVER recent ones (whose
    videos may still be in the CSI window) and NEVER live (pending/tracking)."""
    conn = sqlite3.connect(tmp_path / "activity.db")
    conn.row_factory = sqlite3.Row
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()  # past the 7-day purge window
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()  # still in resurface window

    def _seed(cid, status, updated_at):
        upsert_generated_card(conn, {"card_id": cid, "source": "youtube", "card_type": "diamond", "title": cid, "summary": "s"})
        conn.execute("UPDATE proactive_signal_cards SET status=?, updated_at=? WHERE card_id=?", (status, updated_at, cid))

    _seed("aged_rejected", "rejected", old)
    _seed("aged_deleted", CARD_STATUS_DELETED, old)
    _seed("aged_promoted", "promoted", old)        # legacy status
    _seed("aged_actioned", "actioned", old)
    _seed("recent_rejected", "rejected", recent)   # resurface window -> KEEP
    _seed("live_pending", CARD_STATUS_PENDING, old)
    _seed("live_tracking", CARD_STATUS_TRACKING, old)
    conn.commit()

    purged = purge_aged_terminal_cards(conn)  # default 7-day window
    assert purged == 4  # the four aged terminal rows

    def _exists(cid):
        return conn.execute("SELECT 1 FROM proactive_signal_cards WHERE card_id=?", (cid,)).fetchone() is not None

    assert not _exists("aged_rejected")
    assert not _exists("aged_deleted")
    assert not _exists("aged_promoted")
    assert not _exists("aged_actioned")
    assert _exists("recent_rejected")   # within resurface window -> preserved
    assert _exists("live_pending")      # live -> never purged
    assert _exists("live_tracking")     # live -> never purged

    # Disabled when window <= 0.
    assert purge_aged_terminal_cards(conn, older_than_days=0) == 0


def test_list_cards_live_filter_excludes_terminal(tmp_path):
    """status='live' returns only pending + tracking (the active triage set),
    excluding actioned/rejected/promoted/deleted — the default tab view."""
    conn = sqlite3.connect(tmp_path / "activity.db")
    conn.row_factory = sqlite3.Row

    def _seed(cid, status):
        upsert_generated_card(conn, {"card_id": cid, "source": "youtube", "card_type": "diamond", "title": cid, "summary": "s"})
        conn.execute("UPDATE proactive_signal_cards SET status=? WHERE card_id=?", (status, cid))

    _seed("p", CARD_STATUS_PENDING)
    _seed("t", CARD_STATUS_TRACKING)
    _seed("a", "actioned")
    _seed("r", "rejected")
    _seed("d", CARD_STATUS_DELETED)
    conn.commit()

    live_ids = {c["card_id"] for c in list_cards(conn, status="live", limit=100)}
    assert live_ids == {"p", "t"}

    # 'all' still excludes only deleted (unchanged behaviour).
    all_ids = {c["card_id"] for c in list_cards(conn, status="all", limit=100)}
    assert all_ids == {"p", "t", "a", "r"}


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

    async def _fake_judge(*, title, channel_name, summary_text):
        return {"buildable": True, "reasoning": "stub: code tutorial", "method": "llm"}

    with patch(
        "universal_agent.services.llm_classifier.classify_tutorial_buildability",
        side_effect=_fake_judge,
    ):
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


def test_generate_signal_cards_is_card_only_no_convergence(tmp_path):
    """The autonomous tick's core: generates cards + runs the TTL sweep, but does
    NOT run the LLM-bearing convergence/tutorial syncs (those have their own
    timer). Proven by the returned counts having ONLY the card keys, and by no
    topic signature being written even though the CSI feedstock has rows."""
    csi_db = tmp_path / "csi.db"
    _seed_csi_db(csi_db)
    db = tmp_path / "activity.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)

    counts = generate_signal_cards(conn, csi_db_path=csi_db)

    # Card-only counts shape — no topic_signatures / convergence_events /
    # tutorial_build_tasks keys (that would mean convergence ran).
    assert set(counts) == {"youtube", "discord", "expired", "purged"}
    assert counts["youtube"] >= 1
    # No convergence side effects: no topic signature, no tutorial_build tasks.
    assert get_topic_signature(conn, "video_one_1") is None
    tutorial_tasks = conn.execute(
        "SELECT COUNT(*) AS c FROM task_hub_items WHERE source_kind = 'tutorial_build'"
    ).fetchone()["c"]
    assert tutorial_tasks == 0
    # Cards landed as pending.
    pending = list_cards(conn, status=CARD_STATUS_PENDING, limit=100)
    assert any(c["source"] == "youtube" for c in pending)
