"""Unit tests for write_convergence_candidate and the SQL-cluster CSI sync path.

PR C scope. Covers:
- Deterministic candidate_id derivation from sorted unique video_ids.
- Upserts a convergence_candidates row + queues a Task Hub item with
  the metadata Atlas's skill consumes (preferred_vp, candidate_id,
  index_path).
- Idempotency: re-submitting the same video cluster does NOT queue twice.
- Final verdicts are sticky: ship/skip/defer/error candidates return
  the existing row unchanged.
- Mid-processing recovery: verdict='' rows re-upsert + re-queue.
- The new SQL-only cluster path inside sync_topic_signatures_from_csi
  no longer calls the legacy LLM Track A/B pipeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from universal_agent import task_hub
from universal_agent.services.proactive_convergence import (
    ensure_schema,
    sync_topic_signatures_from_csi,
    upsert_topic_signature,
    write_convergence_candidate,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _expected_candidate_id(video_ids: list[str]) -> str:
    seed = "|".join(sorted({v for v in video_ids if v}))
    return f"cand_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def _signature(video_id: str, channel: str, topic: str = "MCP servers") -> dict:
    return {
        "video_id": video_id,
        "channel_id": channel.lower().replace(" ", "-"),
        "channel_name": channel,
        "video_title": f"{topic} update on {channel}",
        "video_url": f"https://youtube.test/{video_id}",
        "ingested_at": "2026-05-28T14:00:00+00:00",
        "primary_topics": [topic],
        "secondary_topics": [],
        "key_claims": [f"{channel} claim about {topic}"],
    }


# ── candidate_id derivation ───────────────────────────────────────────


def test_candidate_id_is_deterministic_from_sorted_video_ids(tmp_path):
    db_path = tmp_path / "activity.db"
    sigs_unsorted = [
        _signature("vid-z", "Channel A"),
        _signature("vid-m", "Channel B"),
        _signature("vid-a", "Channel C"),
    ]
    expected = _expected_candidate_id(["vid-z", "vid-m", "vid-a"])

    with _connect(db_path) as conn:
        ensure_schema(conn)
        row = write_convergence_candidate(conn, signatures=sigs_unsorted)

    assert row["candidate_id"] == expected
    # Order of signatures must NOT change the hash.
    sigs_other_order = [
        _signature("vid-a", "Channel C"),
        _signature("vid-z", "Channel A"),
        _signature("vid-m", "Channel B"),
    ]
    with _connect(tmp_path / "activity2.db") as conn:
        ensure_schema(conn)
        row2 = write_convergence_candidate(conn, signatures=sigs_other_order)
    assert row2["candidate_id"] == expected


def test_candidate_id_rejects_signatures_with_no_video_ids(tmp_path):
    db_path = tmp_path / "activity.db"
    bad = [{"channel_name": "x", "primary_topics": ["t"]}]
    with _connect(db_path) as conn:
        ensure_schema(conn)
        with pytest.raises(ValueError):
            write_convergence_candidate(conn, signatures=bad)


def test_candidate_id_rejects_empty_signatures(tmp_path):
    db_path = tmp_path / "activity.db"
    with _connect(db_path) as conn:
        ensure_schema(conn)
        with pytest.raises(ValueError):
            write_convergence_candidate(conn, signatures=[])


# ── new candidate → upserts + queues task ─────────────────────────────


def test_new_candidate_upserts_row_and_queues_task(tmp_path):
    db_path = tmp_path / "activity.db"
    sigs = [
        _signature("vid-a", "Channel A"),
        _signature("vid-b", "Channel B"),
    ]
    with _connect(db_path) as conn:
        ensure_schema(conn)
        row = write_convergence_candidate(conn, signatures=sigs)

        # Row persisted.
        stored = conn.execute(
            "SELECT * FROM convergence_candidates WHERE candidate_id = ?",
            (row["candidate_id"],),
        ).fetchone()
        assert stored is not None
        assert stored["verdict"] == ""
        assert stored["task_id"] == row["task_id"]
        assert stored["channel_count"] == 2
        assert sorted(json.loads(stored["video_ids_json"])) == ["vid-a", "vid-b"]
        assert sorted(json.loads(stored["channel_names_json"])) == ["Channel A", "Channel B"]

        # Task Hub item exists.
        task = task_hub.get_item(conn, row["task_id"])
        assert task is not None
        assert task["source_kind"] == "convergence_candidate"
        assert task["agent_ready"] is True
        # Description must direct Atlas to the new skill.
        assert "/evaluate-and-author-intel-brief" in task["description"]
        assert "Do NOT email Kevin directly" in task["description"]

        # Newly queued sentinel was set.
        assert row["_newly_queued"] is True


def test_task_metadata_includes_preferred_vp_and_candidate_id_and_index_path(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "UA_RECENT_BRIEFS_INDEX_PATH",
        "/tmp/test_recent_briefs_index.md",
    )
    db_path = tmp_path / "activity.db"
    sigs = [
        _signature("vid-a", "Channel A"),
        _signature("vid-b", "Channel B"),
    ]
    with _connect(db_path) as conn:
        ensure_schema(conn)
        row = write_convergence_candidate(conn, signatures=sigs)
        task = task_hub.get_item(conn, row["task_id"])

    metadata = task["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    assert metadata["preferred_vp"] == "vp.general.primary"
    assert metadata["candidate_id"] == row["candidate_id"]
    assert metadata["index_path"] == "/tmp/test_recent_briefs_index.md"
    assert metadata["invoke_skill"] == "evaluate-and-author-intel-brief"
    assert metadata["source"] == "convergence_candidate"
    assert sorted(metadata["video_ids"]) == ["vid-a", "vid-b"]


# ── idempotency ───────────────────────────────────────────────────────


def test_resubmitting_same_cluster_does_not_double_queue(tmp_path):
    db_path = tmp_path / "activity.db"
    sigs = [
        _signature("vid-a", "Channel A"),
        _signature("vid-b", "Channel B"),
    ]
    with _connect(db_path) as conn:
        ensure_schema(conn)
        row1 = write_convergence_candidate(conn, signatures=sigs)
        row2 = write_convergence_candidate(conn, signatures=sigs)

        # Same candidate_id; same task_id (upsert).
        assert row1["candidate_id"] == row2["candidate_id"]
        assert row1["task_id"] == row2["task_id"]

        # Exactly one row.
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM convergence_candidates WHERE candidate_id = ?",
            (row1["candidate_id"],),
        ).fetchone()["n"]
        assert n == 1

        # Exactly one Task Hub item — the upsert on task_hub_items uses task_id
        # as PK, so re-queueing returns the same row.
        m = conn.execute(
            "SELECT COUNT(*) AS m FROM task_hub_items WHERE task_id = ?",
            (row1["task_id"],),
        ).fetchone()["m"]
        assert m == 1


def test_candidate_with_final_verdict_returns_existing_no_requeue(tmp_path):
    db_path = tmp_path / "activity.db"
    sigs = [
        _signature("vid-a", "Channel A"),
        _signature("vid-b", "Channel B"),
    ]
    with _connect(db_path) as conn:
        ensure_schema(conn)
        first = write_convergence_candidate(conn, signatures=sigs)

        # Atlas marks it shipped.
        conn.execute(
            """
            UPDATE convergence_candidates
            SET verdict='ship', verdict_reasoning='looks good',
                artifact_id='pa_existing', evaluated_at='2026-05-28T15:00:00+00:00'
            WHERE candidate_id = ?
            """,
            (first["candidate_id"],),
        )
        conn.commit()

        again = write_convergence_candidate(conn, signatures=sigs)

        # Returned the existing row, NOT a fresh upsert.
        assert again["candidate_id"] == first["candidate_id"]
        assert again["verdict"] == "ship"
        assert again["artifact_id"] == "pa_existing"
        # Sentinel flag says "not newly queued".
        assert again.get("_newly_queued") is False


@pytest.mark.parametrize("final_verdict", ["ship", "skip", "defer", "error"])
def test_final_verdict_is_sticky_for_all_terminal_states(tmp_path, final_verdict):
    db_path = tmp_path / "activity.db"
    sigs = [
        _signature("vid-a", "Channel A"),
        _signature("vid-b", "Channel B"),
    ]
    with _connect(db_path) as conn:
        ensure_schema(conn)
        first = write_convergence_candidate(conn, signatures=sigs)
        conn.execute(
            "UPDATE convergence_candidates SET verdict=? WHERE candidate_id=?",
            (final_verdict, first["candidate_id"]),
        )
        conn.commit()

        again = write_convergence_candidate(conn, signatures=sigs)
        assert again["verdict"] == final_verdict
        assert again.get("_newly_queued") is False


def test_mid_processing_candidate_with_empty_verdict_re_upserts_and_requeues(tmp_path):
    db_path = tmp_path / "activity.db"
    sigs = [
        _signature("vid-a", "Channel A"),
        _signature("vid-b", "Channel B"),
    ]
    with _connect(db_path) as conn:
        ensure_schema(conn)
        first = write_convergence_candidate(conn, signatures=sigs)
        # Simulate crash mid-evaluation — verdict still '' (unchanged).
        again = write_convergence_candidate(conn, signatures=sigs)

        # Same candidate; flagged as newly queued (recovery semantics).
        assert again["candidate_id"] == first["candidate_id"]
        assert again["verdict"] == ""
        assert again.get("_newly_queued") is True


# ── sync_topic_signatures_from_csi: SQL clustering replaces LLM ───────


def _build_two_channel_csi(tmp_path) -> Path:
    """Build a CSI db with two channels covering the same topic (a coarse
    two-channel bucket). Returns the db path."""
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
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    for event_id, channel in (("evt-a", "Channel A"), ("evt-b", "Channel B")):
        csi.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, subject_json) VALUES (?, 'youtube_channel_rss', 'channel_new_upload', ?, ?)",
            (
                event_id,
                recent,
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
                json.dumps({"themes": ["MCP servers"], "key_claims": ["MCP is useful."]}),
                recent,
            ),
        )
    csi.commit()
    csi.close()
    return csi_db


def test_sync_topic_signatures_llm_clustering_default(tmp_path):
    """sync_topic_signatures_from_csi now runs the LLM precision layer by
    default (operator decision 2026-05-29 — ZAI quota is abundant). The coarse
    SQL bucket is refined by an LLM judge; a candidate is written only when the
    LLM confirms a genuine shared thesis. The legacy "no LLM after PR C" guard
    is intentionally superseded — see
    docs/proactive_signals/llm_convergence_clustering_2026-05-29.md.
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
    # Use a now-relative timestamp so the row stays inside the sync's default
    # 72h rolling window regardless of the wall-clock date the suite runs on.
    # (A hardcoded date here is a time-bomb: it silently falls out of the window
    # 72h after that date and the test then asserts await_count>=1 against 0.)
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
                json.dumps({"themes": ["MCP servers"], "key_claims": ["MCP is useful."]}),
                # Within the default 72h window of "now".
                None,
            ),
        )
    csi.commit()
    csi.close()

    # The LLM judge confirms the two-channel bucket genuinely converges.
    llm_confirm = AsyncMock(return_value=json.dumps({
        "is_convergence": True,
        "thesis": "Two independent channels cover the same MCP server pattern.",
        "converging_video_ids": ["evt-a", "evt-b"],
        "signal_strength": 9,
    }))
    with patch("universal_agent.services.llm_classifier._call_llm", llm_confirm), \
         _connect(tmp_path / "activity.db") as conn:
        counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)

        # Both signatures upserted; the LLM judge WAS consulted.
        assert counts["upserted"] == 2
        assert llm_confirm.await_count >= 1

        # Exactly one confirmed convergence_candidate row, carrying the thesis.
        rows = conn.execute(
            "SELECT candidate_id, channel_count, video_ids_json, verdict, metadata_json FROM convergence_candidates"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["channel_count"] == 2
        assert sorted(json.loads(rows[0]["video_ids_json"])) == ["evt-a", "evt-b"]
        assert rows[0]["verdict"] == ""

        # And exactly one convergence_candidate Task Hub item.
        task_rows = conn.execute(
            "SELECT task_id, source_kind FROM task_hub_items WHERE source_kind = 'convergence_candidate'"
        ).fetchall()
        assert len(task_rows) == 1


def test_sync_topic_signatures_llm_skip_writes_no_candidate(tmp_path):
    """When the LLM judge rejects the coarse bucket (no genuine convergence),
    NO candidate is written — this is the cluster-quality fix that stops
    over-broad topic buckets from reaching Atlas."""
    csi_db = _build_two_channel_csi(tmp_path)
    llm_reject = AsyncMock(return_value=json.dumps({
        "is_convergence": False, "thesis": "", "converging_video_ids": [], "signal_strength": 0,
    }))
    with patch("universal_agent.services.llm_classifier._call_llm", llm_reject), \
         _connect(tmp_path / "activity.db") as conn:
        counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)
        assert counts["upserted"] == 2
        assert llm_reject.await_count >= 1
        assert conn.execute("SELECT COUNT(*) FROM convergence_candidates").fetchone()[0] == 0


def test_sync_topic_signatures_sql_fallback_when_llm_disabled(tmp_path, monkeypatch):
    """UA_CONVERGENCE_LLM_CLUSTERING=0 falls back to legacy SQL string-match
    clustering and must NOT touch the LLM. The Track B ideation sweep is a
    separate LLM path (own flag) — disable it too to isolate this assertion."""
    monkeypatch.setenv("UA_CONVERGENCE_LLM_CLUSTERING", "0")
    monkeypatch.setenv("UA_IDEATION_SWEEP_ENABLED", "0")
    csi_db = _build_two_channel_csi(tmp_path)
    no_llm = AsyncMock(side_effect=AssertionError("LLM must not run when clustering disabled"))
    with patch("universal_agent.services.llm_classifier._call_llm", no_llm), \
         _connect(tmp_path / "activity.db") as conn:
        counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)
        assert counts["upserted"] == 2
        assert no_llm.await_count == 0
        assert conn.execute("SELECT COUNT(*) FROM convergence_candidates").fetchone()[0] == 1


def test_sync_topic_signatures_skips_clusters_without_multi_channel_coverage(tmp_path):
    """Same channel, multiple videos, same topic → no candidate."""
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
    # Relative timestamp so both events land inside the 72h clustering window
    # on any run date — otherwise this test passes vacuously (rows aged out of
    # the window can't form a cluster, so n==0 regardless of the single-channel
    # rule it means to exercise).
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    for event_id in ("evt-a", "evt-b"):
        csi.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, subject_json) VALUES (?, 'youtube_channel_rss', 'channel_new_upload', ?, ?)",
            (
                event_id,
                recent,
                json.dumps(
                    {
                        "video_id": event_id,
                        "title": "MCP solo",
                        "channel_name": "Channel A",
                        "channel_id": "channel-a",
                        "url": f"https://youtube.test/{event_id}",
                    }
                ),
            ),
        )
        csi.execute(
            "INSERT INTO rss_event_analysis (event_id, transcript_status, category, summary_text, analysis_json, analyzed_at) VALUES (?, 'ok', 'ai', 'one-channel cluster', ?, NULL)",
            (event_id, json.dumps({"themes": ["MCP servers"], "key_claims": ["c"]})),
        )
    csi.commit()
    csi.close()

    with _connect(tmp_path / "activity.db") as conn:
        counts = sync_topic_signatures_from_csi(conn, csi_db_path=csi_db)
        n = conn.execute("SELECT COUNT(*) AS n FROM convergence_candidates").fetchone()["n"]

    assert counts["upserted"] == 2
    assert n == 0
