"""Daily-ceiling enforcement for the tutorial-build auto-route (P2a).

These tests drive ``sync_build_oriented_csi_videos`` end-to-end against a seeded
CSI events DB (mirroring ``test_proactive_signals._seed_csi_db``) with the LLM
judge patched to always say "buildable" (mirroring
``test_tutorial_build_route_guard.test_judge_returns_true_for_genuine_tutorial``).

The ceiling caps how many builds auto-dispatch per America/Chicago day. Builds
over the ceiling are queued as *pending-approval* rows (``agent_ready=False``,
``status=open``) that CODIE never claims until a one-field flip promotes them.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from unittest.mock import patch

from universal_agent import task_hub
from universal_agent.services import proactive_tutorial_builds as ptb
from universal_agent.services.proactive_tutorial_builds import (
    sync_build_oriented_csi_videos,
)


def _connect_activity(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_csi_videos(path: Path, videos: list[dict]) -> None:
    """Seed a CSI events DB with build-oriented YouTube videos.

    Each ``videos`` entry: {event_id, video_id, title, occurred_at,
    transcript_status}. ``id`` autoincrements in insert order; the SELECT in
    ``sync_build_oriented_csi_videos`` orders by ``e.id DESC`` but the producer
    re-ranks by (transcript_ok, occurred_at) desc, so insert order is irrelevant
    to the final ranking.
    """
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
    import json

    for v in videos:
        subject = {
            "video_id": v["video_id"],
            "url": f"https://www.youtube.com/watch?v={v['video_id']}",
            "title": v["title"],
            "description": "Step-by-step coding walkthrough.",
            "channel_name": "AI Builder",
        }
        conn.execute(
            "INSERT INTO events (event_id, source, event_type, occurred_at, subject_json) "
            "VALUES (?, 'youtube_channel_rss', 'channel_new_upload', ?, ?)",
            (v["event_id"], v["occurred_at"], json.dumps(subject)),
        )
        conn.execute(
            "INSERT INTO rss_event_analysis (event_id, transcript_status, transcript_chars, "
            "category, summary_text, analysis_json, analyzed_at) "
            "VALUES (?, ?, ?, 'ai_coding', ?, ?, '2026-04-13 00:05:00')",
            (
                v["event_id"],
                v["transcript_status"],
                12000,
                f"Working build walkthrough for {v['title']}.",
                json.dumps({"language": "python", "themes": ["mcp", "agentic coding"]}),
            ),
        )
    conn.commit()
    conn.close()


async def _judge_buildable(*, title, channel_name, summary_text):
    return {"buildable": True, "reasoning": "concrete build steps", "method": "llm"}


def _run_sync(csi_db: Path, activity_conn: sqlite3.Connection):
    with patch(
        "universal_agent.services.llm_classifier.classify_tutorial_buildability",
        side_effect=_judge_buildable,
    ):
        return sync_build_oriented_csi_videos(activity_conn, csi_db_path=csi_db)


def _build_row(conn: sqlite3.Connection, video_id: str) -> dict | None:
    import hashlib

    task_id = f"tutorial-build:{hashlib.sha256(video_id.encode()).hexdigest()[:16]}"
    return task_hub.get_item(conn, task_id)


# ── (a) today_count < ceiling → top N dispatchable, rest pending ────────────

def test_ceiling_splits_dispatchable_and_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "2")
    csi_db = tmp_path / "csi.db"
    _seed_csi_videos(
        csi_db,
        [
            {"event_id": "yt-a", "video_id": "vid_a", "title": "Build A", "occurred_at": "2026-04-10T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-b", "video_id": "vid_b", "title": "Build B", "occurred_at": "2026-04-12T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-c", "video_id": "vid_c", "title": "Build C", "occurred_at": "2026-04-11T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-d", "video_id": "vid_d", "title": "Build D", "occurred_at": "2026-04-09T00:00:00Z", "transcript_status": "ok"},
        ],
    )
    with _connect_activity(tmp_path / "activity.db") as conn:
        result = _run_sync(csi_db, conn)

        assert result["seen"] == 4
        assert result["ceiling"] == 2
        assert result["today_count"] == 0
        assert result["auto_queued"] == 2
        assert result["pending_approval"] == 2
        assert result["queued"] == 4

        dispatchable = {vid for vid in ("vid_a", "vid_b", "vid_c", "vid_d") if _build_row(conn, vid)["agent_ready"]}
        pending = {vid for vid in ("vid_a", "vid_b", "vid_c", "vid_d") if not _build_row(conn, vid)["agent_ready"]}
        assert len(dispatchable) == 2
        assert len(pending) == 2

        # Pending rows carry the pending-approval representation P2b reads.
        for vid in pending:
            row = _build_row(conn, vid)
            assert row["status"] == task_hub.TASK_STATUS_OPEN
            assert "pending-approval" in row["labels"]
            assert "agent-ready" not in row["labels"]
            assert row["metadata"]["approval_state"] == "pending_approval"


# ── (b) pending rows are NOT claimable (same predicate dispatch uses) ───────

def test_pending_rows_are_not_dispatch_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "1")
    csi_db = tmp_path / "csi.db"
    _seed_csi_videos(
        csi_db,
        [
            {"event_id": "yt-x", "video_id": "vid_x", "title": "Build X", "occurred_at": "2026-04-12T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-y", "video_id": "vid_y", "title": "Build Y", "occurred_at": "2026-04-10T00:00:00Z", "transcript_status": "ok"},
        ],
    )
    with _connect_activity(tmp_path / "activity.db") as conn:
        _run_sync(csi_db, conn)

        # Same eligibility predicate the dispatcher uses (rebuild_dispatch_queue):
        # eligible = agent_ready AND score>=threshold; agent_ready=False removes
        # the row from the eligible set entirely.
        task_hub.rebuild_dispatch_queue(conn)
        eligible_by_task = {
            row["task_id"]: int(row["eligible"])
            for row in conn.execute("SELECT task_id, eligible FROM task_hub_dispatch_queue").fetchall()
        }

        import hashlib

        pending_task_id = f"tutorial-build:{hashlib.sha256(b'vid_y').hexdigest()[:16]}"
        # The over-ceiling row is present in the queue but never eligible.
        assert eligible_by_task.get(pending_task_id, 0) == 0
        assert _build_row(conn, "vid_y")["agent_ready"] is False


# ── (c) ranking respected: transcript-ok + newest dispatched first ──────────

def test_ranking_dispatches_transcript_ok_and_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "1")
    csi_db = tmp_path / "csi.db"
    # The newest transcript-ok video must win the single dispatch slot over an
    # older transcript-ok one and over a newer no-transcript one.
    _seed_csi_videos(
        csi_db,
        [
            {"event_id": "yt-old-ok", "video_id": "vid_old_ok", "title": "Old OK", "occurred_at": "2026-04-01T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-new-ok", "video_id": "vid_new_ok", "title": "New OK", "occurred_at": "2026-04-20T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-newest-pending", "video_id": "vid_newest_pending", "title": "Newest no transcript", "occurred_at": "2026-04-25T00:00:00Z", "transcript_status": "pending"},
        ],
    )
    with _connect_activity(tmp_path / "activity.db") as conn:
        result = _run_sync(csi_db, conn)

        assert result["auto_queued"] == 1
        assert _build_row(conn, "vid_new_ok")["agent_ready"] is True
        # transcript-ok beats the strictly-newer no-transcript video.
        assert _build_row(conn, "vid_newest_pending")["agent_ready"] is False
        assert _build_row(conn, "vid_old_ok")["agent_ready"] is False


# ── (d) idempotency: re-run does not duplicate or demote an approved row ────

def test_rerun_is_idempotent_and_never_demotes(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "1")
    csi_db = tmp_path / "csi.db"
    _seed_csi_videos(
        csi_db,
        [
            {"event_id": "yt-1", "video_id": "vid_1", "title": "Build 1", "occurred_at": "2026-04-12T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-2", "video_id": "vid_2", "title": "Build 2", "occurred_at": "2026-04-10T00:00:00Z", "transcript_status": "ok"},
        ],
    )
    with _connect_activity(tmp_path / "activity.db") as conn:
        first = _run_sync(csi_db, conn)
        assert first["auto_queued"] == 1
        assert _build_row(conn, "vid_1")["agent_ready"] is True

        rows_after_first = conn.execute(
            "SELECT COUNT(*) FROM task_hub_items WHERE source_kind = 'tutorial_build'"
        ).fetchone()[0]
        assert rows_after_first == 2

        # Second run: today_count is now 2 (both rows created today), so the
        # ceiling of 1 leaves remaining=0 — the already-dispatchable vid_1 must
        # NOT be demoted to pending, and no duplicate rows are created.
        second = _run_sync(csi_db, conn)
        assert _build_row(conn, "vid_1")["agent_ready"] is True  # never demoted

        rows_after_second = conn.execute(
            "SELECT COUNT(*) FROM task_hub_items WHERE source_kind = 'tutorial_build'"
        ).fetchone()[0]
        assert rows_after_second == 2  # idempotent: no duplicates
        assert second["today_count"] >= 2


# ── (e) ceiling=0 / env override honored ────────────────────────────────────

def test_ceiling_zero_queues_everything_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "0")
    csi_db = tmp_path / "csi.db"
    _seed_csi_videos(
        csi_db,
        [
            {"event_id": "yt-1", "video_id": "vid_1", "title": "Build 1", "occurred_at": "2026-04-12T00:00:00Z", "transcript_status": "ok"},
            {"event_id": "yt-2", "video_id": "vid_2", "title": "Build 2", "occurred_at": "2026-04-10T00:00:00Z", "transcript_status": "ok"},
        ],
    )
    with _connect_activity(tmp_path / "activity.db") as conn:
        result = _run_sync(csi_db, conn)
        assert result["ceiling"] == 0
        assert result["auto_queued"] == 0
        assert result["pending_approval"] == 2
        assert _build_row(conn, "vid_1")["agent_ready"] is False
        assert _build_row(conn, "vid_2")["agent_ready"] is False


def test_ceiling_default_is_ten_when_env_unset(monkeypatch):
    monkeypatch.delenv("UA_DEMO_BUILD_DAILY_CEILING", raising=False)
    assert ptb._daily_build_ceiling() == 10


def test_ceiling_clamps_negative_to_zero(monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "-5")
    assert ptb._daily_build_ceiling() == 0


def test_ceiling_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "not-a-number")
    assert ptb._daily_build_ceiling() == 10
