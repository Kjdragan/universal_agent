from __future__ import annotations

from datetime import datetime, timezone
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


def test_sync_topic_signatures_from_csi_writes_convergence_candidate(tmp_path):
    """sync_topic_signatures_from_csi runs SQL recall → LLM precision (the
    2026-05-29 cluster-quality fix). When the LLM judge confirms a genuine
    convergence, a ``convergence_candidate`` row + Task Hub item is written.

    Tests for the legacy ``detect_and_queue_convergence`` LLM path stay
    intact above; the gateway hand-trigger endpoints still call that
    function until PR E cleans it up.
    """
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
    # Use a recent timestamp so SQL clustering's 72h rolling window includes us.
    recent_iso = datetime.now(timezone.utc).isoformat()
    for event_id, channel in (("evt-a", "Channel A"), ("evt-b", "Channel B")):
        csi.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, subject_json) VALUES (?, 'youtube_channel_rss', 'channel_new_upload', ?, ?)",
            (
                event_id,
                recent_iso,
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
            "INSERT INTO rss_event_analysis (event_id, transcript_status, category, summary_text, analysis_json, analyzed_at) VALUES (?, 'ok', 'ai', 'MCP server pattern for agents', ?, ?)",
            (
                event_id,
                json.dumps({"themes": ["MCP servers"], "key_claims": ["MCP is useful for agent tools."]}),
                recent_iso,
            ),
        )
    csi.commit()
    csi.close()

    with _connect(tmp_path / "activity.db") as conn:
        # The LLM precision layer confirms the two-channel bucket converges.
        llm_confirm = AsyncMock(return_value=json.dumps({
            "is_convergence": True,
            "thesis": "Two independent channels cover the same MCP server pattern.",
            "converging_video_ids": ["evt-a", "evt-b"],
            "signal_strength": 9,
        }))
        with patch("universal_agent.services.llm_classifier._call_llm", llm_confirm):
            counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)
        assert llm_confirm.await_count >= 1

        # The confirmed cluster should write exactly one convergence_candidate row.
        rows = conn.execute(
            "SELECT candidate_id, channel_count, verdict FROM convergence_candidates"
        ).fetchall()
        # Verify the Task Hub item too.
        task_rows = conn.execute(
            "SELECT task_id, source_kind FROM task_hub_items WHERE source_kind='convergence_candidate'"
        ).fetchall()

    assert counts["upserted"] == 2
    assert counts["candidates_written"] >= 1
    assert len(rows) == 1
    assert rows[0]["channel_count"] == 2
    assert rows[0]["verdict"] == ""
    assert len(task_rows) == 1


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


# === Framing-clause regression (2026-05-18 incident) ===
#
# Three Simone-authored emails on 2026-05-18 opened with "here's the X you
# asked for" framing even though every one was triggered by the
# csi_convergence_sync cron, not by Kevin. Root cause: the proactive task
# description omitted any framing directive, so the executing LLM defaulted
# to operator-requested phrasing. These tests pin the corrective directive
# into both brief task description builders so it can't silently regress.


_REQUIRED_FRAMING_PHRASES = (
    "FRAMING:",
    "csi_convergence_sync",
    "Kevin did not ask for this",
    "Do NOT say 'as you requested'",
    "proactive discovery",
)

_BANNED_OPERATOR_REQUESTED_PHRASES = (
    "make it suitable for Simone review email.",
)


def test_convergence_brief_description_has_proactive_framing_clause():
    from universal_agent.services.proactive_convergence import _brief_task_description

    signatures = [
        {
            "video_id": "v1",
            "channel_id": "c1",
            "channel_name": "Channel One",
            "video_title": "MCP servers",
            "video_url": "https://youtube.test/v1",
            "key_claims": ["x"],
        },
        {
            "video_id": "v2",
            "channel_id": "c2",
            "channel_name": "Channel Two",
            "video_title": "MCP again",
            "video_url": "https://youtube.test/v2",
            "key_claims": ["y"],
        },
    ]
    desc = _brief_task_description(primary_topic="MCP servers", signatures=signatures)

    for phrase in _REQUIRED_FRAMING_PHRASES:
        assert phrase in desc, f"convergence brief description missing required clause: {phrase!r}"
    for banned in _BANNED_OPERATOR_REQUESTED_PHRASES:
        assert banned not in desc, (
            f"convergence brief description still contains banned operator-requested phrasing: {banned!r}"
        )


def test_insight_brief_description_has_proactive_framing_clause(tmp_path):
    from universal_agent.services.proactive_convergence import create_insight_brief_task

    db_path = tmp_path / "activity.db"
    signatures = [
        {
            "video_id": "v1",
            "channel_id": "c1",
            "channel_name": "Channel One",
            "video_title": "Trend A",
            "video_url": "https://youtube.test/v1",
            "key_claims": ["a"],
        },
        {
            "video_id": "v2",
            "channel_id": "c2",
            "channel_name": "Channel Two",
            "video_title": "Trend B",
            "video_url": "https://youtube.test/v2",
            "key_claims": ["b"],
        },
    ]
    with _connect(db_path) as conn:
        result = create_insight_brief_task(
            conn,
            narrative="A non-obvious macro-trend across AI tooling",
            value="High — informs next quarter strategy",
            signatures=signatures,
        )
        task = task_hub.get_item(conn, result["task"]["task_id"])

    desc = task["description"]
    for phrase in _REQUIRED_FRAMING_PHRASES:
        assert phrase in desc, f"insight brief description missing required clause: {phrase!r}"
    for banned in _BANNED_OPERATOR_REQUESTED_PHRASES:
        assert banned not in desc, (
            f"insight brief description still contains banned operator-requested phrasing: {banned!r}"
        )
