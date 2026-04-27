from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent import task_hub
from universal_agent.services.proactive_convergence import (
    detect_and_queue_convergence,
    extract_topic_signature_from_text,
    sync_topic_signatures_from_csi,
    track_a_concrete_convergence,
    track_b_ideation_synthesis,
    upsert_topic_signature,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_detect_and_queue_convergence_for_independent_channels(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        first = upsert_topic_signature(
            conn,
            video_id="video-a",
            channel_id="channel-a",
            channel_name="Channel A",
            video_title="New MCP pattern",
            video_url="https://youtube.test/a",
            ingested_at="2026-04-15T10:00:00+00:00",
            primary_topics=["MCP servers"],
            key_claims=["MCP servers are becoming agent infrastructure."],
        )
        second = upsert_topic_signature(
            conn,
            video_id="video-b",
            channel_id="channel-b",
            channel_name="Channel B",
            video_title="Why MCP matters",
            video_url="https://youtube.test/b",
            ingested_at="2026-04-15T11:00:00+00:00",
            primary_topics=["MCP servers"],
            key_claims=["MCP is central for agent tool integration."],
        )

        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                json.dumps({"matches": [{"video_id": "video-a", "reason": "match"}], "signal_strength": 9}),
                json.dumps({"insights": [{"narrative": "MCP is rising", "value": "Actionable", "supporting_video_ids": ["video-a", "video-b"]}]})
            ]
            results = detect_and_queue_convergence(conn, signature=second)
            
        task_a = None
        task_b = None
        event_a = None
        event_b = None
        
        for result in results:
            if result["task"]["source_kind"] == "convergence_detection":
                task_a = task_hub.get_item(conn, result["task"]["task_id"])
                event_a = result["event"]
            elif result["task"]["source_kind"] == "insight_detection":
                task_b = task_hub.get_item(conn, result["task"]["task_id"])
                event_b = result["event"]

    assert first["video_id"] == "video-a"
    assert len(results) == 2
    assert event_a["primary_topic"] == "MCP servers"
    assert set(event_a["channel_names"]) == {"Channel A", "Channel B"}
    assert task_a is not None
    assert task_a["source_kind"] == "convergence_detection"
    assert task_a["agent_ready"] is True
    assert "CONVERGENCE SIGNAL" in task_a["description"]
    
    assert event_b["primary_topic"] == "MCP is rising"
    assert task_b is not None
    assert task_b["source_kind"] == "insight_detection"
    assert "THE INSIGHT" in task_b["description"]


def test_convergence_requires_multiple_channels(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        first = upsert_topic_signature(
            conn,
            video_id="video-a",
            channel_id="same-channel",
            channel_name="Channel A",
            ingested_at="2026-04-15T10:00:00+00:00",
            primary_topics=["MCP servers"],
        )
        second = upsert_topic_signature(
            conn,
            video_id="video-b",
            channel_id="same-channel",
            channel_name="Channel A",
            ingested_at="2026-04-15T11:00:00+00:00",
            primary_topics=["MCP servers"],
        )

        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                json.dumps({"matches": [{"video_id": "video-a"}], "signal_strength": 9}),
                json.dumps({"insights": [{"narrative": "MCP", "value": "val", "supporting_video_ids": ["video-a", "video-b"]}]})
            ]
            results = detect_and_queue_convergence(conn, signature=second)

    assert first["channel_id"] == "same-channel"
    assert len(results) == 0


def test_sync_topic_signatures_from_csi_creates_convergence(tmp_path):
    csi_db = tmp_path / "csi.db"
    csi = sqlite3.connect(csi_db)
    csi.execute(
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
    csi.execute(
        """
        CREATE TABLE rss_event_analysis (
            event_id TEXT UNIQUE NOT NULL,
            transcript_status TEXT,
            category TEXT,
            summary_text TEXT,
            analysis_json TEXT,
            analyzed_at TEXT
        )
        """
    )
    for event_id, channel in (("evt-a", "Channel A"), ("evt-b", "Channel B")):
        csi.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, subject_json) VALUES (?, 'youtube_channel_rss', 'channel_new_upload', '2026-04-15T10:00:00+00:00', ?)",
            (
                event_id,
                json.dumps(
                    {
                        "video_id": event_id,
                        "title": "MCP server pattern",
                        "channel_name": channel,
                        "channel_id": channel.lower().replace(" ", "-"),
                        "url": f"https://youtube.test/{event_id}",
                    }
                ),
            ),
        )
        csi.execute(
            "INSERT INTO rss_event_analysis (event_id, transcript_status, category, summary_text, analysis_json, analyzed_at) VALUES (?, 'ok', 'ai', 'MCP server pattern for agents', ?, '2026-04-15T11:00:00+00:00')",
            (event_id, json.dumps({"themes": ["MCP servers"], "key_claims": ["MCP is useful for agent tools."]})),
        )
    csi.commit()
    csi.close()

    with _connect(tmp_path / "activity.db") as conn:
        with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                json.dumps({"matches": [{"video_id": "evt-a"}], "signal_strength": 9}),
                json.dumps({"insights": [{"narrative": "MCP trend", "value": "val", "supporting_video_ids": ["evt-a", "evt-b"]}]})
            ]
            counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)

    assert counts["upserted"] == 2
    assert counts["convergence_events"] >= 1


@pytest.mark.asyncio
async def test_extract_topic_signature_from_text_uses_llm_json():
    response = """
    {"primary_topics":["MCP servers"],"secondary_topics":["agent tools"],"key_claims":["MCP connects agents to tools."],"content_type":"analysis"}
    """
    with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = response
        signature = await extract_topic_signature_from_text(
            video_id="video-llm",
            title="Why MCP matters",
            summary_text="MCP connects agents to tools.",
        )

    assert signature["primary_topics"] == ["MCP servers"]
    assert signature["content_type"] == "analysis"
    assert signature["metadata"]["signature_method"] == "llm"


@pytest.mark.asyncio
async def test_track_a_concrete_convergence_returns_empty_on_bad_json():
    signature = {
        "video_id": "new",
        "primary_topics": ["MCP servers"],
        "secondary_topics": [],
    }
    candidates = [
        {"video_id": "old", "primary_topics": ["MCP servers"], "secondary_topics": []}
    ]
    with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "not json"
        matched = await track_a_concrete_convergence(signature, candidates)

    assert matched == []


@pytest.mark.asyncio
async def test_track_b_ideation_synthesis_returns_empty_on_bad_json():
    batch = [
        {"video_id": "a", "primary_topics": ["MCP"]},
        {"video_id": "b", "primary_topics": ["MCP"]}
    ]
    with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "not json"
        results = await track_b_ideation_synthesis(batch)
        
    assert results == []

