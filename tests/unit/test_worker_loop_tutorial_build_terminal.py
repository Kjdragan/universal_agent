"""Component E — completion-bridge fix for the proactive tutorial_build lane.

``tutorial_build`` had no dedicated terminal branch in
``worker_loop._handle_mission``: a completed mission fell to the default
``else`` close, which set ``status=completed`` but NEVER stamped a
``completion_token``. Because the head-of-line guard keys on that token, a
retry/exhaustion sweep could revert the finished demo to ``open`` and re-claim
it ("re-surface").

``_close_tutorial_build_demo_source_task`` is the explicit terminal branch
(mirrors ``cody_demo_task``): it completes the source task through the canonical
demo-lane verb, which enforces the completion-evidence gate AND stamps a
non-empty ``completion_token`` — so a re-opened finished demo is no longer
dispatchable.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub
from universal_agent.vp.worker_loop import _close_tutorial_build_demo_source_task


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_delegated_tutorial_build(conn, task_id="tutorial-build:e2e1"):
    """An in-flight tutorial_build source task (delegated to a VP mission)."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "tutorial_build",
            "source_ref": "demo-video",
            "title": "Build private tutorial repo: e2e",
            "description": "build it",
            "status": task_hub.TASK_STATUS_DELEGATED,
            "metadata": {"delegation": {"mission_id": "vp-mission-e2e"}},
        },
    )
    return task_id


def test_completed_tutorial_build_closes_with_completion_token():
    conn = _conn()
    task_id = _seed_delegated_tutorial_build(conn)

    result = _close_tutorial_build_demo_source_task(
        conn,
        source_task_id=task_id,
        mission_id="vp-mission-e2e",
        vp_id="vp.coder.primary",
        terminal_meta={"vp_terminal_status": "completed", "result_ref": ""},
        tutorial_finalize={"ok": True, "demo_id": "demo-proactive-e2e"},
    )

    assert result["status"] == task_hub.TASK_STATUS_COMPLETED
    row = task_hub.get_item(conn, task_id)
    assert row["status"] == task_hub.TASK_STATUS_COMPLETED
    # The head-of-line guard the default close never set.
    assert str(row.get("completion_token") or "").strip()
    # Finalize evidence is persisted on the row.
    assert (row["metadata"].get("demo_finalize") or {}).get("ok") is True


def test_completed_tutorial_build_does_not_resurface_after_reopen():
    conn = _conn()
    task_id = _seed_delegated_tutorial_build(conn, task_id="tutorial-build:nosurf")

    _close_tutorial_build_demo_source_task(
        conn,
        source_task_id=task_id,
        mission_id="vp-mission-nosurf",
        vp_id="vp.coder.primary",
        terminal_meta={"vp_terminal_status": "completed", "result_ref": ""},
        tutorial_finalize={"ok": True, "demo_id": "demo-proactive-nosurf"},
    )
    token = str(task_hub.get_item(conn, task_id).get("completion_token") or "").strip()
    assert token

    # Simulate the re-surfacing vector: a sweep blindly reverts the finished
    # row to open + agent_ready. The completion_token must NOT be cleared by an
    # upsert, so the head-of-line guard keeps it out of the claimable set.
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "priority": 4,
        },
    )
    # The token survives the re-open (upsert never touches completion_token).
    assert str(task_hub.get_item(conn, task_id).get("completion_token") or "").strip() == token

    claimed = task_hub.claim_next_dispatch_tasks(conn, limit=10, agent_id="heartbeat")
    claimed_ids = {str(c.get("task_id") or "") for c in claimed}
    assert task_id not in claimed_ids
