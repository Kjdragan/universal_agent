"""Unit tests for the hourly insight-email composer.

Covers:
- Floor filtering / scoring composite math
- Diversity selection (Jaccard <0.30 for insight #2)
- Sub-threshold filler path when no candidates clear the floor
- None return when no candidates exist in the window
- Idempotency of scoring-log writes within the same delivery slot
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

import pytest

from universal_agent.services import (
    proactive_artifacts as _pa,
    proactive_scoring_log as _scoring,
)
from universal_agent.services.hourly_insight_email import (
    DIVERSITY_MAX_OVERLAP,
    compose_hourly_email,
)


def _connect(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "activity.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_brief(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    title: str,
    summary: str = "",
    topic_tags: list[str] | None = None,
    confidence: float = 0.8,
    supporting_channel_count: int = 4,
    created_at: str | None = None,
    artifact_type: str = "insight_brief_task",
) -> dict[str, Any]:
    """Insert a brief artifact for tests, bypassing the upsert helper."""
    _pa.ensure_schema(conn)
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    metadata = {
        "task_id": f"task-{artifact_id}",
        "event_id": f"event-{artifact_id}",
        "video_ids": [f"v-{i}" for i in range(supporting_channel_count)],
        "confidence": float(confidence),
        "supporting_channel_count": int(supporting_channel_count),
    }
    conn.execute(
        """
        INSERT INTO proactive_artifacts (
            artifact_id, artifact_type, source_kind, source_ref, title, summary,
            status, delivery_state, priority, artifact_uri, artifact_path,
            source_url, topic_tags_json, metadata_json, feedback_json,
            created_at, updated_at
        ) VALUES (?, ?, 'insight_detection', '', ?, ?, 'candidate', 'not_surfaced',
                  3, '', '', '', ?, ?, '{}', ?, ?)
        """,
        (
            artifact_id,
            artifact_type,
            title,
            summary,
            json.dumps(topic_tags or []),
            json.dumps(metadata),
            stamp,
            stamp,
        ),
    )
    conn.commit()
    return _pa.get_artifact(conn, artifact_id) or {}


def test_returns_none_when_no_candidates(tmp_path) -> None:
    conn = _connect(tmp_path)
    try:
        result = compose_hourly_email(conn, hour_window_hours=1)
        assert result is None
    finally:
        conn.close()


def test_composes_single_insight_when_only_one_candidate(tmp_path) -> None:
    conn = _connect(tmp_path)
    try:
        _insert_brief(
            conn,
            artifact_id="art-1",
            title="ATLAS insight brief: MCP servers convergence",
            summary="Three independent channels converged on MCP server tooling.",
            topic_tags=["insight", "MCP servers"],
            confidence=0.85,
            supporting_channel_count=4,
        )
        result = compose_hourly_email(conn, hour_window_hours=1)
        assert result is not None
        assert result["insight_2"] is None
        assert result["met_floor"][0] is True
        assert result["met_floor"][1] is False
        assert "[Hourly Intel]" in result["subject"]
        assert "MCP servers convergence" in result["subject"]
    finally:
        conn.close()


def test_diversity_picks_low_overlap_second(tmp_path) -> None:
    """Insight #2 must have Jaccard topic overlap < 0.30 with #1."""
    conn = _connect(tmp_path)
    try:
        # #1 — highest score; tags = {insight, mcp servers}
        _insert_brief(
            conn,
            artifact_id="hi",
            title="ATLAS insight brief: MCP wave",
            topic_tags=["insight", "mcp servers"],
            confidence=0.95,
            supporting_channel_count=5,
        )
        # Near-duplicate (Jaccard with #1 should be ~1.0) — should NOT be picked.
        _insert_brief(
            conn,
            artifact_id="dup",
            title="ATLAS insight brief: MCP redux",
            topic_tags=["insight", "mcp servers"],
            confidence=0.92,
            supporting_channel_count=5,
        )
        # Disjoint topic (overlap 0) — should be picked as #2.
        _insert_brief(
            conn,
            artifact_id="div",
            title="ATLAS insight brief: agentic browsers",
            topic_tags=["agentic browsers", "browser automation"],
            confidence=0.80,
            supporting_channel_count=4,
        )
        result = compose_hourly_email(conn, hour_window_hours=1)
        assert result is not None
        assert result["insight_1"]["artifact"]["artifact_id"] == "hi"
        assert result["insight_2"] is not None
        assert result["insight_2"]["artifact"]["artifact_id"] == "div"
    finally:
        conn.close()


def test_sub_threshold_filler_when_no_one_clears(tmp_path) -> None:
    """When zero candidates meet floor, still surface the top-scored brief."""
    conn = _connect(tmp_path)
    try:
        _insert_brief(
            conn,
            artifact_id="weak",
            title="ATLAS insight brief: weak signal",
            topic_tags=["weak signal"],
            confidence=0.40,  # below floor
            supporting_channel_count=2,  # below floor
        )
        result = compose_hourly_email(conn, hour_window_hours=1)
        assert result is not None
        assert result["met_floor"][0] is False
        # Scoring-log row should be tagged as sub_threshold_filler.
        row = conn.execute(
            "SELECT delivery_slot FROM proactive_brief_scoring_log WHERE artifact_id='weak'"
        ).fetchone()
        assert row is not None
        assert row["delivery_slot"] == _scoring.SLOT_SUB_THRESHOLD_FILLER
    finally:
        conn.close()


def test_floor_clears_when_thresholds_met(tmp_path) -> None:
    conn = _connect(tmp_path)
    try:
        _insert_brief(
            conn,
            artifact_id="ok",
            title="ATLAS insight brief: clear signal",
            topic_tags=["clear signal"],
            confidence=0.75,
            supporting_channel_count=3,
        )
        result = compose_hourly_email(conn, hour_window_hours=1)
        assert result is not None
        assert result["met_floor"][0] is True
        row = conn.execute(
            "SELECT delivery_slot FROM proactive_brief_scoring_log WHERE artifact_id='ok'"
        ).fetchone()
        assert row is not None
        assert row["delivery_slot"] == _scoring.SLOT_INSIGHT_1
    finally:
        conn.close()


def test_scoring_log_is_idempotent_within_slot(tmp_path) -> None:
    """The same (artifact_id, delivery_slot, logged_at) cannot be inserted twice."""
    conn = _connect(tmp_path)
    try:
        _scoring.ensure_schema(conn)
        stamp = "2026-05-25T12:00:00+00:00"
        first = _scoring.log_score(
            conn,
            artifact_id="art-1",
            confidence=0.8,
            channel_breadth=4,
            novelty=0.9,
            preference_bonus=0.5,
            composite_score=0.75,
            met_floor=True,
            delivered_hourly=True,
            delivery_slot=_scoring.SLOT_INSIGHT_1,
            logged_at=stamp,
        )
        assert first
        # Re-inserting with the same key must be a no-op (INSERT OR IGNORE).
        _scoring.log_score(
            conn,
            artifact_id="art-1",
            confidence=0.9,  # different value
            channel_breadth=5,
            novelty=0.95,
            preference_bonus=0.6,
            composite_score=0.85,
            met_floor=True,
            delivered_hourly=True,
            delivery_slot=_scoring.SLOT_INSIGHT_1,
            logged_at=stamp,
        )
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM proactive_brief_scoring_log WHERE artifact_id='art-1'"
        ).fetchone()
        assert count["c"] == 1
    finally:
        conn.close()


def test_excludes_already_delivered(tmp_path) -> None:
    """Briefs with delivered_at stamped MUST NOT be re-considered."""
    conn = _connect(tmp_path)
    try:
        _insert_brief(
            conn,
            artifact_id="delivered",
            title="ATLAS insight brief: already sent",
            topic_tags=["already sent"],
            confidence=0.9,
            supporting_channel_count=5,
        )
        _pa.mark_artifact_delivered(conn, artifact_id="delivered")
        result = compose_hourly_email(conn, hour_window_hours=1)
        assert result is None  # only candidate was already delivered
    finally:
        conn.close()


def test_window_filtering(tmp_path) -> None:
    """Briefs created outside the window MUST NOT be considered."""
    conn = _connect(tmp_path)
    try:
        old_stamp = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        _insert_brief(
            conn,
            artifact_id="old",
            title="ATLAS insight brief: stale",
            topic_tags=["stale"],
            confidence=0.9,
            supporting_channel_count=5,
            created_at=old_stamp,
        )
        result = compose_hourly_email(conn, hour_window_hours=1)
        assert result is None
    finally:
        conn.close()


def test_diversity_threshold_constant() -> None:
    """Pin the diversity threshold so it doesn't silently regress."""
    assert DIVERSITY_MAX_OVERLAP == pytest.approx(0.30)
