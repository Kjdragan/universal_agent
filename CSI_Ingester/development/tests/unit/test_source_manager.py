"""Tests for csi_ingester.store.source_manager."""

from __future__ import annotations

import json
from pathlib import Path

from csi_ingester.store.source_manager import (
    auto_promote_demote,
    get_active_reddit_sources,
    get_active_threads_terms,
    get_active_youtube_channels,
    get_source_summary,
    record_quality_assessment,
    seed_reddit_sources,
    seed_threads_terms,
    seed_youtube_channels,
)
from csi_ingester.store.sqlite import connect, ensure_schema
import pytest


@pytest.fixture()
def db_conn(tmp_path: Path):
    db = connect(tmp_path / "test.db")
    ensure_schema(db)
    yield db
    db.close()


# ── Seed tests ──────────────────────────────────────────────────────────

def test_seed_youtube_channels(db_conn, tmp_path: Path):
    seed = {
        "channels": [
            {
                "channel_id": "UC_TEST1",
                "channel_name": "AICodeKing",
                "video_count": 32,
                "rss_feed_url": "https://youtube.com/feeds/videos.xml?channel_id=UC_TEST1",
                "youtube_url": "https://youtube.com/channel/UC_TEST1",
            },
            {
                "channel_id": "UC_TEST2",
                "channel_name": "Pyotr Kurzin | Geopolitics",
                "video_count": 5,
                "rss_feed_url": "https://youtube.com/feeds/videos.xml?channel_id=UC_TEST2",
                "youtube_url": "https://youtube.com/channel/UC_TEST2",
            },
            {
                "channel_id": "UC_TEST3",
                "channel_name": "Chef Billy Parisi",
                "video_count": 2,
                "rss_feed_url": "",
                "youtube_url": "",
            },
        ]
    }
    seed_file = tmp_path / "channels.json"
    seed_file.write_text(json.dumps(seed))

    count = seed_youtube_channels(db_conn, seed_file)
    assert count == 3

    channels = get_active_youtube_channels(db_conn)
    assert len(channels) == 3

    # Check auto-classification
    by_id = {ch["channel_id"]: ch for ch in channels}
    assert by_id["UC_TEST1"]["domain"] == "ai_coding"   # "Code" in name
    assert by_id["UC_TEST1"]["tier"] == 1                # 32 videos → tier 1
    assert by_id["UC_TEST2"]["domain"] == "geopolitics"  # "Geopolitics" in name
    assert by_id["UC_TEST2"]["tier"] == 1                # 5 videos → tier 1
    assert by_id["UC_TEST3"]["domain"] == "other_signal" # Chef → non-signal
    assert by_id["UC_TEST3"]["tier"] == 3                # 2 videos → tier 3


def test_seed_youtube_idempotent(db_conn, tmp_path: Path):
    """Re-seeding should update name/url but not overwrite runtime state."""
    seed = {
        "channels": [
            {"channel_id": "UC_X", "channel_name": "AI Channel", "video_count": 10},
        ]
    }
    seed_file = tmp_path / "channels.json"
    seed_file.write_text(json.dumps(seed))

    seed_youtube_channels(db_conn, seed_file)

    # Simulate runtime state change
    db_conn.execute("UPDATE youtube_channels SET quality_score = 0.9 WHERE channel_id = 'UC_X'")
    db_conn.commit()

    # Re-seed
    seed_youtube_channels(db_conn, seed_file)

    row = db_conn.execute("SELECT quality_score FROM youtube_channels WHERE channel_id = 'UC_X'").fetchone()
    assert float(row["quality_score"]) == 0.9  # Not overwritten


def test_seed_reddit_sources(db_conn, tmp_path: Path):
    seed = {
        "subreddits": [
            {"name": "LocalLLaMA", "domain": "ai_models", "tier": 1, "note": "User sub"},
            {"name": "geopolitics", "domain": "geopolitics", "tier": 2},
        ]
    }
    seed_file = tmp_path / "reddit.json"
    seed_file.write_text(json.dumps(seed))

    count = seed_reddit_sources(db_conn, seed_file)
    assert count == 2

    sources = get_active_reddit_sources(db_conn)
    assert len(sources) == 2
    assert sources[0]["subreddit"] == "LocalLLaMA"  # tier 1 sorts first


def test_seed_threads_terms(db_conn):
    packs = [
        {"name": "ai-models", "domain": "ai_models", "terms": ["LLM", "foundation model"]},
        {"name": "economics", "domain": "economics", "terms": ["interest rates"]},
    ]
    count = seed_threads_terms(db_conn, packs)
    assert count == 3

    terms = get_active_threads_terms(db_conn)
    assert len(terms) == 3


# ── Query tests ─────────────────────────────────────────────────────────

def test_filter_by_domain(db_conn, tmp_path: Path):
    seed = {
        "channels": [
            {"channel_id": "UC_AI", "channel_name": "AI Lab", "video_count": 10},
            {"channel_id": "UC_GEO", "channel_name": "Geopolitics Daily", "video_count": 5},
        ]
    }
    seed_file = tmp_path / "channels.json"
    seed_file.write_text(json.dumps(seed))
    seed_youtube_channels(db_conn, seed_file)

    ai_only = get_active_youtube_channels(db_conn, domain="ai_models")
    assert len(ai_only) == 1
    assert ai_only[0]["channel_id"] == "UC_AI"


def test_filter_by_tier(db_conn, tmp_path: Path):
    seed = {
        "channels": [
            {"channel_id": "UC_T1", "channel_name": "AI Expert", "video_count": 10},
            {"channel_id": "UC_T3", "channel_name": "AI Beginner", "video_count": 1},
        ]
    }
    seed_file = tmp_path / "channels.json"
    seed_file.write_text(json.dumps(seed))
    seed_youtube_channels(db_conn, seed_file)

    tier1 = get_active_youtube_channels(db_conn, max_tier=1)
    assert len(tier1) == 1
    assert tier1[0]["channel_id"] == "UC_T1"


# ── Quality scoring tests ──────────────────────────────────────────────

def test_record_quality_assessment(db_conn, tmp_path: Path):
    seed = {"channels": [{"channel_id": "UC_Q", "channel_name": "Test AI", "video_count": 5}]}
    sf = tmp_path / "channels.json"
    sf.write_text(json.dumps(seed))
    seed_youtube_channels(db_conn, sf)

    score = record_quality_assessment(
        db_conn,
        source_type="youtube",
        source_key="UC_Q",
        relevance=0.8,
        engagement=0.6,
        novelty=0.7,
        confidence=0.9,
        items_count=15,
    )

    # Weighted: 0.8*0.4 + 0.6*0.2 + 0.7*0.2 + 0.9*0.2 = 0.32 + 0.12 + 0.14 + 0.18 = 0.76
    assert abs(score - 0.76) < 0.01

    # Check it updated the channel record
    row = db_conn.execute("SELECT quality_score, items_assessed FROM youtube_channels WHERE channel_id = 'UC_Q'").fetchone()
    assert abs(float(row["quality_score"]) - 0.76) < 0.01
    assert int(row["items_assessed"]) == 15

    # Check history record
    history = db_conn.execute("SELECT * FROM source_quality_history WHERE source_key = 'UC_Q'").fetchall()
    assert len(history) == 1


def test_auto_promote_demote(db_conn, tmp_path: Path):
    seed = {
        "channels": [
            {"channel_id": "UC_GOOD", "channel_name": "Top AI Channel", "video_count": 3},
            {"channel_id": "UC_BAD", "channel_name": "Low AI Channel", "video_count": 5},
        ]
    }
    sf = tmp_path / "channels.json"
    sf.write_text(json.dumps(seed))
    seed_youtube_channels(db_conn, sf)

    # Give UC_GOOD high scores
    for _ in range(3):
        record_quality_assessment(
            db_conn, source_type="youtube", source_key="UC_GOOD",
            relevance=0.9, engagement=0.8, novelty=0.8, confidence=0.9, items_count=5,
        )

    # Give UC_BAD low scores
    for _ in range(3):
        record_quality_assessment(
            db_conn, source_type="youtube", source_key="UC_BAD",
            relevance=0.1, engagement=0.1, novelty=0.1, confidence=0.1, items_count=5,
        )

    result = auto_promote_demote(db_conn, source_type="youtube", min_assessments=3)

    assert "UC_GOOD" in result["promoted"]
    assert "UC_BAD" in result["demoted"]

    # Verify tier changes persisted
    good = db_conn.execute("SELECT tier FROM youtube_channels WHERE channel_id = 'UC_GOOD'").fetchone()
    bad = db_conn.execute("SELECT tier FROM youtube_channels WHERE channel_id = 'UC_BAD'").fetchone()
    assert int(good["tier"]) == 1
    assert int(bad["tier"]) == 3


# ── Summary test ────────────────────────────────────────────────────────

def test_get_source_summary(db_conn, tmp_path: Path):
    seed = {
        "channels": [
            {"channel_id": "UC_A", "channel_name": "AI Expert", "video_count": 10},
            {"channel_id": "UC_B", "channel_name": "Tech Guy", "video_count": 1},
        ]
    }
    sf = tmp_path / "channels.json"
    sf.write_text(json.dumps(seed))
    seed_youtube_channels(db_conn, sf)

    summary = get_source_summary(db_conn)
    assert summary["youtube"]["total"] == 2
    assert "ai_models" in summary["youtube"]["by_domain"] or "ai_coding" in summary["youtube"]["by_domain"]
