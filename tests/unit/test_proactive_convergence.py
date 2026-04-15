from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from universal_agent import task_hub
from universal_agent.services.proactive_convergence import (
    detect_and_queue_convergence,
    extract_topic_signature_from_text,
    llm_match_signatures,
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

        result = detect_and_queue_convergence(conn, signature=second)
        task = task_hub.get_item(conn, result["task"]["task_id"])

    assert first["video_id"] == "video-a"
    assert result is not None
    assert result["event"]["primary_topic"] == "MCP servers"
    assert set(result["event"]["channel_names"]) == {"Channel A", "Channel B"}
    assert task is not None
    assert task["source_kind"] == "convergence_detection"
    assert task["agent_ready"] is True
    assert "CONVERGENCE SIGNAL" in task["description"]
    assert result["artifact"]["artifact_type"] == "convergence_brief_task"


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

        result = detect_and_queue_convergence(conn, signature=second)

    assert first["channel_id"] == "same-channel"
    assert result is None


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
async def test_llm_match_signatures_falls_back_to_overlap_on_bad_json():
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
        matched = await llm_match_signatures(signature, candidates)

    assert [item["video_id"] for item in matched] == ["old"]
