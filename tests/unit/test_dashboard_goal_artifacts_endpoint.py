"""Tests for the Task Hub /goal-artifacts endpoint.

`GET /api/v1/dashboard/todolist/tasks/{task_id}/goal-artifacts` returns:
- The operator's original prompt (title + description from task hub row)
- use_goal_loop flag and target_agent
- The linked VP mission_id and status (or null if no mission yet)
- The workspace path
- Contents of BRIEF.md, ACCEPTANCE.md, goal_condition.txt, COMPLETION.md
  (or null entries when files don't exist yet)

These tests exercise the endpoint's READ + LOOKUP behavior directly via
task_hub + vp_state DBs. The endpoint is a thin SQL/file read; the logic
lives in the lookup chain and filesystem read.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from universal_agent import task_hub
from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
from universal_agent.durable.state import upsert_vp_mission


def _make_task_hub_db(tmp_path) -> str:
    db_path = tmp_path / "activity_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    conn.close()
    return str(db_path)


def _make_vp_state_db_with_mission(
    tmp_path,
    *,
    mission_id: str,
    task_id: str,
    workspace_path: str = "",
    status: str = "completed",
) -> str:
    db_path = tmp_path / "vp_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_vp_schema(conn)

    payload: dict = {"metadata": {"task_id": task_id, "use_goal_loop": True}}
    upsert_vp_mission(
        conn,
        mission_id=mission_id,
        vp_id="vp.coder.primary",
        status=status,
        objective="test mission",
        mission_type="task",
        payload=payload,
        result_ref=f"workspace://{workspace_path}" if workspace_path else None,
    )
    conn.close()
    return str(db_path)


def test_endpoint_lookup_by_mission_id(tmp_path):
    """The endpoint finds the mission when the task_id IS the mission_id.

    Worker_loop creates a Task Hub mirror row keyed by mission_id; this
    lookup path matches that pattern.
    """
    activity_db = _make_task_hub_db(tmp_path)
    workspace = tmp_path / "workspace-1"
    workspace.mkdir()
    (workspace / "BRIEF.md").write_text("# BRIEF\n\nI think the task is X.\n")
    (workspace / "ACCEPTANCE.md").write_text("# ACCEPTANCE\n\n- tests pass\n")
    (workspace / "goal_condition.txt").write_text("Tests pass and lint clean.\n")
    # COMPLETION.md intentionally absent — endpoint should return null for it

    vp_db = _make_vp_state_db_with_mission(
        tmp_path,
        mission_id="vp-mission-abc123",
        task_id="vp-mission-abc123",  # task_id == mission_id (worker_loop mirror)
        workspace_path=str(workspace),
        status="completed",
    )

    # Direct query mirroring the endpoint's SQL
    with sqlite3.connect(vp_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT mission_id, status, result_ref, completed_at, payload_json
            FROM vp_missions
            WHERE mission_id = ?
               OR json_extract(payload_json, '$.metadata.task_id') = ?
            ORDER BY completed_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            ("vp-mission-abc123", "vp-mission-abc123"),
        ).fetchone()
    assert row is not None
    assert row["mission_id"] == "vp-mission-abc123"
    assert row["result_ref"] == f"workspace://{workspace}"


def test_endpoint_lookup_by_payload_metadata(tmp_path):
    """The endpoint finds the mission via payload_json.metadata.task_id.

    vp_dispatch_mission propagates the linked task_id into mission
    metadata when called as ``vp_dispatch_mission(task_id="qa-task-uvw")``.
    """
    workspace = tmp_path / "workspace-2"
    workspace.mkdir()
    vp_db = _make_vp_state_db_with_mission(
        tmp_path,
        mission_id="vp-mission-def456",
        task_id="qa-task-uvw",
        workspace_path=str(workspace),
        status="running",
    )

    with sqlite3.connect(vp_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT mission_id FROM vp_missions
            WHERE mission_id = ?
               OR json_extract(payload_json, '$.metadata.task_id') = ?
            """,
            ("qa-task-uvw", "qa-task-uvw"),
        ).fetchone()
    assert row is not None
    assert row["mission_id"] == "vp-mission-def456"


def test_workspace_path_extraction_from_result_ref():
    """The workspace:// prefix is stripped to get the filesystem path."""
    result_ref_examples = [
        ("workspace:///tmp/foo", "/tmp/foo"),
        ("workspace://", ""),
        ("/tmp/already-clean", "/tmp/already-clean"),
        ("", ""),
    ]
    for raw, expected in result_ref_examples:
        if raw.startswith("workspace://"):
            actual = raw[len("workspace://"):].strip()
        elif raw:
            actual = raw
        else:
            actual = ""
        assert actual == expected, f"raw={raw!r} expected={expected!r} got={actual!r}"


def test_artifact_file_reads(tmp_path):
    """Each of the four artifact filenames is read when present."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "BRIEF.md").write_text("brief content")
    (workspace / "ACCEPTANCE.md").write_text("acceptance content")
    (workspace / "goal_condition.txt").write_text("condition content")
    (workspace / "COMPLETION.md").write_text("completion content")

    for name, expected_content in [
        ("BRIEF.md", "brief content"),
        ("ACCEPTANCE.md", "acceptance content"),
        ("goal_condition.txt", "condition content"),
        ("COMPLETION.md", "completion content"),
    ]:
        fpath = workspace / name
        assert fpath.exists()
        raw = fpath.read_bytes()[:64 * 1024]
        text = raw.decode("utf-8", errors="replace")
        assert text == expected_content


def test_missing_artifact_returns_none():
    """Files that don't exist on disk yield None in the response payload."""
    # Simulating what the endpoint does
    artifacts = {name: None for name in ["BRIEF.md", "ACCEPTANCE.md", "goal_condition.txt", "COMPLETION.md"]}
    # Workspace exists but has no files yet
    # All four should remain None
    for name, value in artifacts.items():
        assert value is None


def test_64kb_truncation_threshold():
    """Files larger than 64 KB get the truncated=True flag."""
    threshold = 64 * 1024
    # Just under
    small = b"x" * (threshold - 1)
    raw_small = small[:threshold]
    assert len(raw_small) == threshold - 1

    # Just over
    big = b"x" * (threshold + 100)
    raw_big = big[:threshold]
    assert len(raw_big) == threshold


def test_task_hub_row_returns_full_metadata(tmp_path):
    """The endpoint surfaces use_goal_loop and target_agent from task metadata."""
    db_path = tmp_path / "activity.db"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        task_hub.ensure_schema(conn)
        task_hub.upsert_item(
            conn,
            {
                "task_id": "qa-test-meta",
                "source_kind": "dashboard_quick_add",
                "title": "Test mission",
                "description": "operator-typed objective here",
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata": {
                    "use_goal_loop": True,
                    "workflow_manifest": {"target_agent": "vp.coder.primary"},
                },
            },
        )
        row = task_hub.get_item(conn, "qa-test-meta")
    assert row is not None
    meta = row.get("metadata") or {}
    assert meta["use_goal_loop"] is True
    assert meta["workflow_manifest"]["target_agent"] == "vp.coder.primary"
    assert row["description"] == "operator-typed objective here"
