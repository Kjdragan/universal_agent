"""Operator approve path for pending-approval tutorial builds (P2b).

P2a (``sync_build_oriented_csi_videos``) queues over-ceiling builds as
pending-approval rows: status=open, agent_ready=False, labels
["pending-approval", "tutorial-build", "codie", "code"],
metadata.approval_state="pending_approval". P2b promotes one with the
canonical one-field flip (agent_ready 0->1) through
``dispatch_service.dispatch_on_approval``, wrapped by
``proactive_tutorial_builds.approve_pending_tutorial_build``. Manual
approvals are UNCAPPED — the approve path never consults
``UA_DEMO_BUILD_DAILY_CEILING``.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import proactive_tutorial_builds as ptb
from universal_agent.services.dispatch_service import DispatchError


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_build(conn: sqlite3.Connection, video_id: str, *, agent_ready: bool = False) -> str:
    """Seed a P2a-shaped tutorial_build row; returns its task_id."""
    queued = ptb.queue_tutorial_build_task(
        conn,
        video_id=video_id,
        video_title=f"Build {video_id}",
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        channel_name="AI Builder",
        agent_ready=agent_ready,
    )
    return str(queued["task"]["task_id"])


# ── (i) approve flips agent_ready and claims ────────────────────────────────

def test_approve_flips_agent_ready_and_claims():
    conn = _make_conn()
    task_id = _seed_build(conn, "vid_pending")
    before = task_hub.get_item(conn, task_id)
    assert before["agent_ready"] is False
    assert "pending-approval" in before["labels"]

    result = ptb.approve_pending_tutorial_build(
        conn, task_id=task_id, agent_id="dashboard_operator"
    )

    assert result["task_id"] == task_id
    assert result["trigger_type"] == "human_approved"
    assert result["assignment_id"]

    after = task_hub.get_item(conn, task_id)
    assert after["agent_ready"] is True
    assert after["status"] == task_hub.TASK_STATUS_IN_PROGRESS
    label_set = {v.lower() for v in after["labels"]}
    assert "pending-approval" not in label_set
    assert "agent-ready" in label_set
    assert after["metadata"]["approval_state"] == "approved"
    assert after["metadata"]["approved_by"] == "dashboard_operator"
    # P2a payload survives the metadata merge.
    assert after["metadata"]["video_id"] == "vid_pending"

    # Approved row leaves the pending list.
    assert ptb.list_pending_approval_builds(conn) == []
    conn.close()


# ── manual approvals are UNCAPPED ───────────────────────────────────────────

def test_approve_ignores_daily_ceiling(monkeypatch):
    """Ceiling=0 blocks ALL auto-dispatch (P2a) but must not block the button."""
    monkeypatch.setenv("UA_DEMO_BUILD_DAILY_CEILING", "0")
    conn = _make_conn()
    task_id = _seed_build(conn, "vid_capped")
    result = ptb.approve_pending_tutorial_build(conn, task_id=task_id)
    assert result["task_id"] == task_id
    assert task_hub.get_item(conn, task_id)["agent_ready"] is True
    conn.close()


# ── (ii) invalid rows raise DispatchError ───────────────────────────────────

def test_approve_missing_row_raises():
    conn = _make_conn()
    with pytest.raises(DispatchError, match="not found"):
        ptb.approve_pending_tutorial_build(conn, task_id="tutorial-build:nope")
    conn.close()


def test_approve_terminal_row_raises():
    conn = _make_conn()
    task_id = _seed_build(conn, "vid_done")
    task_hub.upsert_item(conn, {"task_id": task_id, "status": task_hub.TASK_STATUS_COMPLETED})
    with pytest.raises(DispatchError, match="terminal"):
        ptb.approve_pending_tutorial_build(conn, task_id=task_id)
    conn.close()


def test_approve_already_dispatchable_raises():
    conn = _make_conn()
    task_id = _seed_build(conn, "vid_auto", agent_ready=True)
    with pytest.raises(DispatchError, match="not pending approval"):
        ptb.approve_pending_tutorial_build(conn, task_id=task_id)
    conn.close()


def test_approve_non_tutorial_row_raises():
    conn = _make_conn()
    task_hub.upsert_item(
        conn,
        {
            "task_id": "generic-1",
            "source_kind": "internal",
            "title": "Generic pending row",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": False,
            "labels": ["pending-approval"],
        },
    )
    with pytest.raises(DispatchError, match="not a tutorial build"):
        ptb.approve_pending_tutorial_build(conn, task_id="generic-1")
    conn.close()


# ── (iii) pending list returns only pending-approval tutorial builds ────────

def test_pending_list_returns_only_pending_tutorial_build_rows():
    conn = _make_conn()
    pending_id = _seed_build(conn, "vid_a")           # pending-approval
    _seed_build(conn, "vid_b", agent_ready=True)      # dispatchable — excluded
    task_hub.upsert_item(                             # wrong source_kind — excluded
        conn,
        {
            "task_id": "generic-2",
            "source_kind": "internal",
            "title": "Not a build",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": False,
            "labels": ["pending-approval"],
        },
    )

    builds = ptb.list_pending_approval_builds(conn)
    assert [b["task_id"] for b in builds] == [pending_id]
    row = builds[0]
    assert row["video_id"] == "vid_a"
    assert row["video_url"] == "https://www.youtube.com/watch?v=vid_a"
    assert row["title"].startswith("Build private tutorial repo:")
    assert row["channel_name"] == "AI Builder"
    assert row["approval_state"] == "pending_approval"
    assert row["created_at"]
    conn.close()
