"""Tests for the ``vp_mission`` mirror-row suppression in the Kanban
projections.

Post PR #522/#525 the parent task row already carries every field the
mirror row exposed (``dispatch.cody_mission_id``, ``cody_workspace_dir``,
Workspace deep-link), so showing both makes the operator click through
two identical cards per Cody run. The list functions now suppress mirror
rows whenever a parent row references them via
``metadata.dispatch.cody_mission_id`` or ``metadata.linked_mission_id``.
Orphan mirrors (no parent) still render — they're not redundant.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_parent(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    mission_id: str,
    status: str = task_hub.TASK_STATUS_COMPLETED,
    parent_link_field: str = "cody_mission_id",
) -> None:
    """Insert a parent row that references a VP mission via metadata.

    ``parent_link_field`` chooses which JSON path holds the mission id —
    the suppression filter checks both ``dispatch.cody_mission_id`` and
    top-level ``linked_mission_id``.
    """
    metadata: dict[str, Any] = {}
    if parent_link_field == "cody_mission_id":
        metadata = {"dispatch": {"cody_mission_id": mission_id}}
    elif parent_link_field == "linked_mission_id":
        metadata = {"linked_mission_id": mission_id}
    else:
        raise ValueError(parent_link_field)
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "dashboard_quick_add",
            "title": f"parent {task_id}",
            "description": "operator request",
            "status": status,
            "metadata": metadata,
            "agent_ready": True,
        },
    )


def _seed_mirror(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    status: str = task_hub.TASK_STATUS_COMPLETED,
) -> None:
    """Insert a vp_mission visibility mirror row."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "vp_mission",
            "title": f"mirror {task_id}",
            "description": "",
            "status": status,
            "agent_ready": False,
        },
    )


def _completed_ids(conn: sqlite3.Connection) -> set[str]:
    return {str(row["task_id"]) for row in task_hub.list_completed_tasks(conn, limit=100)}


def _queue_ids(conn: sqlite3.Connection) -> set[str]:
    queue = task_hub.list_agent_queue(conn, limit=100, include_not_ready=True)
    return {str(item["task_id"]) for item in queue["items"]}


def test_completed_list_suppresses_mirror_when_parent_references_via_cody_mission_id():
    conn = _conn()
    _seed_parent(conn, task_id="qa-001", mission_id="vp-mission-aaa")
    _seed_mirror(conn, task_id="vp-mission-aaa")

    ids = _completed_ids(conn)

    assert "qa-001" in ids
    assert "vp-mission-aaa" not in ids


def test_completed_list_suppresses_mirror_when_parent_references_via_linked_mission_id():
    conn = _conn()
    _seed_parent(conn, task_id="brief-001", mission_id="vp-mission-bbb", parent_link_field="linked_mission_id")
    _seed_mirror(conn, task_id="vp-mission-bbb")

    ids = _completed_ids(conn)

    assert "brief-001" in ids
    assert "vp-mission-bbb" not in ids


def test_orphan_mirror_still_appears_in_completed_list():
    """A vp_mission row with NO referencing parent must still render.

    Some signal-curation flows go straight to a VP without a Task Hub
    parent — those mirror rows are the ONLY surface the operator has
    for that work.
    """
    conn = _conn()
    _seed_mirror(conn, task_id="vp-mission-orphan")

    ids = _completed_ids(conn)
    assert "vp-mission-orphan" in ids


def test_agent_queue_suppresses_mirror_when_parent_exists():
    conn = _conn()
    _seed_parent(
        conn,
        task_id="qa-active",
        mission_id="vp-mission-active",
        status=task_hub.TASK_STATUS_DELEGATED,
    )
    _seed_mirror(
        conn,
        task_id="vp-mission-active",
        status=task_hub.TASK_STATUS_DELEGATED,
    )

    ids = _queue_ids(conn)

    assert "qa-active" in ids
    assert "vp-mission-active" not in ids


def test_agent_queue_keeps_orphan_mirror():
    conn = _conn()
    _seed_mirror(
        conn,
        task_id="vp-mission-orphan-active",
        status=task_hub.TASK_STATUS_OPEN,
    )

    ids = _queue_ids(conn)
    assert "vp-mission-orphan-active" in ids


def test_non_vp_mission_rows_unaffected():
    """Suppression must not collateral-damage other source_kinds."""
    conn = _conn()
    task_hub.upsert_item(
        conn,
        {
            "task_id": "regular-001",
            "source_kind": "email",
            "title": "regular email task",
            "description": "",
            "status": task_hub.TASK_STATUS_COMPLETED,
        },
    )
    task_hub.upsert_item(
        conn,
        {
            "task_id": "regular-002",
            "source_kind": "csi",
            "title": "regular csi task",
            "description": "",
            "status": task_hub.TASK_STATUS_COMPLETED,
        },
    )

    ids = _completed_ids(conn)
    assert "regular-001" in ids
    assert "regular-002" in ids


def test_parent_and_mirror_self_reference_not_counted():
    """Defense against a degenerate row that references itself in
    metadata — the filter must not exclude such rows on the basis of a
    self-link."""
    conn = _conn()
    task_hub.upsert_item(
        conn,
        {
            "task_id": "vp-mission-self",
            "source_kind": "vp_mission",
            "title": "self ref",
            "description": "",
            "status": task_hub.TASK_STATUS_COMPLETED,
            "metadata": {"dispatch": {"cody_mission_id": "vp-mission-self"}},
        },
    )

    ids = _completed_ids(conn)
    # Self-reference must NOT trigger suppression (no other row references it).
    assert "vp-mission-self" in ids
