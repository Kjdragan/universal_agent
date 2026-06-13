"""P3: the curated Daily Digest queues demo-worthy videos into the
tutorial_build Task Hub lane through the SAME daily ceiling as the broad CSI
sweep (proactive_tutorial_builds.queue_tutorial_builds_with_ceiling), with
structural cross-source dedupe on sha256(video_id) task ids."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.scripts import youtube_daily_digest as ydd
from universal_agent.services.proactive_tutorial_builds import (
    queue_tutorial_builds_with_ceiling,
)


def _conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "activity.db")
    conn.row_factory = sqlite3.Row
    return conn


def _row(video_id: str, score: int, *, prospect: bool = True, rank: int = 1) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "title": f"Video {video_id}",
        "value_score": score,
        "value_tier": "high",
        "code_implementation_prospect": prospect,
        "evidence_quality": "transcript",
        "reason": "fixture",
        "rank": rank,
    }


def _rss_candidate(video_id: str) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "video_title": f"RSS {video_id}",
        "video_url": "",
        "channel_name": "",
        "extraction_plan": {},
        "priority": 3,
    }


def _task(conn: sqlite3.Connection, video_id: str):
    task_id = f"tutorial-build:{hashlib.sha256(video_id.encode()).hexdigest()[:16]}"
    return task_hub.get_item(conn, task_id)


def test_digest_demo_worthy_rows_queue_through_shared_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "2")
    decisions = {
        "ranked_videos": [
            _row("dig_a", 95, rank=1),
            _row("dig_b", 90, rank=2),
            _row("dig_c", 85, rank=3),
            _row("dig_no", 88, rank=4, prospect=False),
        ]
    }
    # Tutorial-tier selection budget (top_n=1) must NOT bound the Demo lane.
    ydd._select_tutorial_dispatch_candidates(decisions, top_n=1, min_score=70)
    with _conn(tmp_path) as conn:
        outcome = ydd._queue_demo_builds(decisions=decisions, dry_run=False, conn=conn)
        assert outcome["candidates"] == 3
        assert outcome["auto_queued"] == 2
        assert outcome["pending_approval"] == 1
        assert _task(conn, "dig_a")["agent_ready"] is True
        assert _task(conn, "dig_b")["agent_ready"] is True
        pending = _task(conn, "dig_c")
        assert pending["agent_ready"] is False
        assert "pending-approval" in pending["labels"]
        assert pending["metadata"]["approval_state"] == "pending_approval"
        assert pending["metadata"]["source"] == "youtube_daily_digest"
        assert _task(conn, "dig_no") is None  # gate-rejected row never queues


def test_dry_run_queues_nothing(tmp_path):
    decisions = {"ranked_videos": [_row("dig_dry", 95)]}
    ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    with _conn(tmp_path) as conn:
        outcome = ydd._queue_demo_builds(decisions=decisions, dry_run=True, conn=conn)
        assert outcome["queued"] == 0
        assert outcome["skipped"] == "dry_run"
        assert _task(conn, "dig_dry") is None


def test_same_video_from_both_sources_yields_one_row(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "1")
    decisions = {"ranked_videos": [_row("shared_vid", 92)]}
    ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    with _conn(tmp_path) as conn:
        first = ydd._queue_demo_builds(decisions=decisions, dry_run=False, conn=conn)
        assert first["auto_queued"] == 1
        # Broad CSI lane sees the SAME video later, with the budget exhausted:
        # the no-churn guard must keep it dispatchable, and no second row appears.
        queue_tutorial_builds_with_ceiling(
            conn, [_rss_candidate("shared_vid")], source="csi_auto_route"
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM task_hub_items WHERE source_kind = 'tutorial_build'"
        ).fetchone()[0]
        assert count == 1
        assert _task(conn, "shared_vid")["agent_ready"] is True  # never demoted


def test_digest_consumes_shared_daily_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "3")
    decisions = {"ranked_videos": [_row("dig_1", 95, rank=1), _row("dig_2", 90, rank=2)]}
    ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    with _conn(tmp_path) as conn:
        ydd._queue_demo_builds(decisions=decisions, dry_run=False, conn=conn)
        # The broad sweep later the same day only has 1 budget slot left.
        outcome = queue_tutorial_builds_with_ceiling(
            conn,
            [_rss_candidate("rss_1"), _rss_candidate("rss_2")],
            source="csi_auto_route",
        )
        assert outcome["today_count"] == 2
        assert outcome["auto_queued"] == 1
        assert outcome["pending_approval"] == 1


def test_auto_route_kill_switch_gates_digest_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_TUTORIAL_AUTO_ROUTE", "0")
    decisions = {"ranked_videos": [_row("dig_off", 95)]}
    ydd._select_tutorial_dispatch_candidates(decisions, top_n=4, min_score=70)
    with _conn(tmp_path) as conn:
        outcome = ydd._queue_demo_builds(decisions=decisions, dry_run=False, conn=conn)
        assert outcome.get("disabled") is True
        assert _task(conn, "dig_off") is None
