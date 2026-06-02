"""Regression: transition_to_pending_review must refresh the mission pointer.

A re-run (iteration 2+) demo that completes via the VP-event bridge
(`transition_to_pending_review`) must repoint `linked_mission_id` /
`dispatch.cody_mission_id` at the mission that just completed, so the dashboard
Workspace button deep-links to the successful run instead of a stale earlier
(often failed) attempt.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_delegated(conn, *, task_id, source_kind, new_mission, stale_mission, workspace):
    """Seed a delegated task whose delegation points at the NEW (completing)
    mission, but whose pointer fields still hold a STALE earlier mission."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "source_ref": "x",
            "title": "t",
            "status": task_hub.TASK_STATUS_DELEGATED,
            "metadata": {
                "workspace_dir": workspace,
                "linked_mission_id": stale_mission,
                "delegation": {"mission_id": new_mission},
                "dispatch": {
                    "cody_mission_id": stale_mission,
                    "cody_workspace_dir": f"{workspace}/vp-mission-{stale_mission}",
                },
            },
        },
    )


def test_demo_pointer_repointed_to_completing_mission():
    conn = _conn()
    _seed_delegated(
        conn,
        task_id="cody_demo_task:t1",
        source_kind="cody_demo_task",
        new_mission="vp-mission-NEW",
        stale_mission="vp-mission-OLD",
        workspace="/opt/ua_demos/code-review__demo-1",
    )
    task_hub.transition_to_pending_review(conn, mission_id="vp-mission-NEW")

    item = task_hub.get_item(conn, "cody_demo_task:t1")
    assert item["status"] == task_hub.TASK_STATUS_PENDING_REVIEW
    md = item["metadata"]
    assert md["linked_mission_id"] == "vp-mission-NEW"
    assert md["dispatch"]["cody_mission_id"] == "vp-mission-NEW"
    # Demo workspace points at the ROOT (where the successful build writes),
    # not the stale per-mission subdir.
    assert md["dispatch"]["cody_workspace_dir"] == "/opt/ua_demos/code-review__demo-1"


def test_non_demo_only_sets_linked_mission_id():
    """Non-demo VP missions get linked_mission_id refreshed but their dispatch
    trace is left untouched (don't change Atlas/general Workspace behavior)."""
    conn = _conn()
    _seed_delegated(
        conn,
        task_id="insight_brief:t2",
        source_kind="insight_brief",
        new_mission="vp-mission-NEW",
        stale_mission="vp-mission-OLD",
        workspace="/tmp/x",
    )
    task_hub.transition_to_pending_review(conn, mission_id="vp-mission-NEW")

    item = task_hub.get_item(conn, "insight_brief:t2")
    assert item["status"] == task_hub.TASK_STATUS_PENDING_REVIEW
    md = item["metadata"]
    assert md["linked_mission_id"] == "vp-mission-NEW"
    # dispatch trace unchanged for non-demo tasks
    assert md["dispatch"]["cody_mission_id"] == "vp-mission-OLD"
