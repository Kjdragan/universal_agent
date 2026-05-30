from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent import task_hub
from universal_agent.services.proactive_convergence import (
    extract_topic_signature_from_text,
    get_topic_signature,
    sync_topic_signatures_from_csi,
    track_b_ideation_synthesis,
    upsert_topic_signature,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _build_csi_db(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Create a minimal CSI db with (event_id, channel, category, title) rows."""
    csi = sqlite3.connect(db_path)
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
    recent_iso = datetime.now(timezone.utc).isoformat()
    for event_id, channel, category, title in rows:
        csi.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, subject_json) VALUES (?, 'youtube_channel_rss', 'channel_new_upload', ?, ?)",
            (
                event_id,
                recent_iso,
                json.dumps(
                    {
                        "video_id": event_id,
                        "title": title,
                        "channel_name": channel,
                        "channel_id": channel.lower().replace(" ", "-"),
                        "url": f"https://youtube.test/{event_id}",
                    }
                ),
            ),
        )
        csi.execute(
            "INSERT INTO rss_event_analysis (event_id, transcript_status, category, summary_text, analysis_json, analyzed_at) VALUES (?, 'ok', ?, ?, ?, ?)",
            (
                event_id,
                category,
                f"{title} summary",
                json.dumps({"themes": [title], "key_claims": [f"{title} is notable."]}),
                recent_iso,
            ),
        )
    csi.commit()
    csi.close()


def test_sync_topic_signatures_excludes_denylisted_categories(tmp_path, monkeypatch):
    """The relevance gate (default ON) drops non-domain categories at ingest so
    geopolitics/cooking/health/noise videos never become topic signatures (and
    therefore never become ideation candidates). Domain categories pass."""
    monkeypatch.delenv("UA_RELEVANCE_GATE_ENABLED", raising=False)
    monkeypatch.delenv("UA_IDEATION_RELEVANCE_DENYLIST", raising=False)
    csi_db = tmp_path / "csi.db"
    _build_csi_db(
        csi_db,
        [
            ("vid-geo", "Geo Channel", "geopolitics_and_conflict", "UK arms embargo to Israel"),
            ("vid-cook", "Food Channel", "cooking", "Perfect sourdough"),
            ("vid-ai", "AI Channel", "ai_coding_and_agents", "Building agents with MCP"),
            ("vid-swe", "Dev Channel", "software_engineering", "Refactoring patterns"),
        ],
    )

    with _connect(tmp_path / "activity.db") as conn:
        # No-op LLM so the convergence/ideation passes don't hit the network.
        no_llm = AsyncMock(return_value="not json")
        with patch("universal_agent.services.llm_classifier._call_llm", no_llm):
            counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)

        # Only the two domain-relevant videos become signatures.
        assert counts["upserted"] == 2
        assert get_topic_signature(conn, "vid-ai")
        assert get_topic_signature(conn, "vid-swe")
        assert get_topic_signature(conn, "vid-geo") is None
        assert get_topic_signature(conn, "vid-cook") is None


def test_sync_topic_signatures_relevance_gate_disabled_keeps_all(tmp_path, monkeypatch):
    """UA_RELEVANCE_GATE_ENABLED=0 restores legacy behaviour: every category is
    ingested, including non-domain ones."""
    monkeypatch.setenv("UA_RELEVANCE_GATE_ENABLED", "0")
    monkeypatch.delenv("UA_IDEATION_RELEVANCE_DENYLIST", raising=False)
    csi_db = tmp_path / "csi.db"
    _build_csi_db(
        csi_db,
        [
            ("vid-geo", "Geo Channel", "geopolitics_and_conflict", "UK arms embargo to Israel"),
            ("vid-ai", "AI Channel", "ai_coding_and_agents", "Building agents with MCP"),
        ],
    )

    with _connect(tmp_path / "activity.db") as conn:
        no_llm = AsyncMock(return_value="not json")
        with patch("universal_agent.services.llm_classifier._call_llm", no_llm):
            counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)

        assert counts["upserted"] == 2
        assert get_topic_signature(conn, "vid-geo")
        assert get_topic_signature(conn, "vid-ai")


def test_relevance_gate_matches_live_csi_category_vocabulary(tmp_path, monkeypatch):
    """Regression for the 2026-05-30 vocabulary-mismatch defect: the live CSI
    classifier emits single-token categories (`geopolitics`, `conflict`,
    `economics`, `from`), NOT the compound taxonomy (`geopolitics_and_conflict`)
    the original handoff assumed. The default denylist MUST exclude the actual
    non-domain values, while keeping every AI/dev category (incl. the mixed
    `technology` bucket, which coarse gating intentionally keeps — intra-category
    mixing is Stage 2's job)."""
    monkeypatch.delenv("UA_RELEVANCE_GATE_ENABLED", raising=False)
    monkeypatch.delenv("UA_IDEATION_RELEVANCE_DENYLIST", raising=False)
    csi_db = tmp_path / "csi.db"
    _build_csi_db(
        csi_db,
        [
            # Non-domain — MUST be excluded (the live values that leaked in v1).
            ("vid-geo", "Geo Channel", "geopolitics", "Ukrainian drones strike Russian refinery"),
            ("vid-conflict", "War Channel", "conflict", "Snipers trade rifles for drone control"),
            ("vid-econ", "Macro Channel", "economics", "Saylor's $300T Bitcoin credit bet"),
            ("vid-from", "Junk Channel", "from", "CENTCOM hits 90 targets on Iran"),
            ("vid-cook", "Food Channel", "cooking", "Perfect sourdough"),
            # Domain — MUST be kept.
            ("vid-coding", "Dev Channel", "ai_coding", "Vibe coding with Claude"),
            ("vid-models", "Research Channel", "ai_models", "Gemini 3 benchmarks"),
            ("vid-tech", "Tech Channel", "technology", "The perfect setup for vibe coding"),
            ("vid-bus", "Biz Channel", "ai_business", "Will AI replace programmers"),
        ],
    )

    with _connect(tmp_path / "activity.db") as conn:
        no_llm = AsyncMock(return_value="not json")
        with patch("universal_agent.services.llm_classifier._call_llm", no_llm):
            counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)

        # 4 domain videos kept; 5 non-domain dropped.
        assert counts["upserted"] == 4
        for kept in ("vid-coding", "vid-models", "vid-tech", "vid-bus"):
            assert get_topic_signature(conn, kept), f"{kept} should be kept"
        for dropped in ("vid-geo", "vid-conflict", "vid-econ", "vid-from", "vid-cook"):
            assert get_topic_signature(conn, dropped) is None, f"{dropped} should be excluded"


def test_sync_topic_signatures_from_csi_writes_convergence_candidate(tmp_path):
    """sync_topic_signatures_from_csi runs SQL recall → LLM precision (the
    2026-05-29 cluster-quality fix). When the LLM judge confirms a genuine
    convergence, a ``convergence_candidate`` row + Task Hub item is written.

    The legacy per-signature LLM path (``detect_and_queue_convergence`` /
    ``track_a_concrete_convergence``) and its hand-trigger endpoints were
    removed 2026-05; this SQL-recall + LLM-precision path is canonical.
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
# into the active candidate task description builder so it can't silently regress.


def test_candidate_task_description_has_proactive_framing():
    from universal_agent.services.proactive_convergence import (
        _candidate_task_description,
    )

    desc = _candidate_task_description(
        candidate_id="cand_test",
        headline="MCP servers",
        candidate_count=2,
        channel_count=2,
        index_path="",
        thesis="Two independent channels cover the same MCP release.",
        signal_strength=8.0,
    )
    # The active convergence_candidate task must never imply Kevin requested
    # this, and must route through the authoring skill (not email directly).
    assert "FRAMING:" in desc
    assert "csi_convergence_sync" in desc
    assert "Kevin did not ask for this" in desc
    assert "proactive-discovery" in desc
    assert "Do NOT frame this as 'as you requested'" in desc
    assert "evaluate-and-author-intel-brief" in desc
    assert "Do NOT email Kevin directly" in desc
