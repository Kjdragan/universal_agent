"""Tests for the VP COMPLETION REVIEW allowlist in the heartbeat composer.

Regression coverage for the crying-wolf false positive where the
pending_review filter was a BLOCKLIST (source_kind != "cody_demo_task"), so
reflection proposals parked in pending_review/needs_review leaked into the
"VP completed, sign off" block every heartbeat despite never having been
delegated to any VP. The filter is now an ALLOWLIST: a row surfaces only
when its metadata.delegation.mission_id resolves to a real mission id
(transition_to_pending_review finds tasks BY that id, so every genuine VP
completion carries it).
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.heartbeat_service import _compose_heartbeat_prompt


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    task_hub.ensure_schema(db)
    yield db
    db.close()


def _seed_pending_review(conn, *, task_id, source_kind, metadata):
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "source_ref": "x",
            "title": f"title for {task_id}",
            "status": task_hub.TASK_STATUS_PENDING_REVIEW,
            "metadata": metadata,
        },
    )


def _compose(conn) -> str:
    return _compose_heartbeat_prompt(
        "BASE",
        investigation_only=False,
        task_hub_claims=[],
        runtime_conn=conn,
    )


def test_reflection_row_without_mission_does_not_surface(conn):
    """A reflection proposal in pending_review has no delegation.mission_id —
    it must NOT be framed as a completed VP mission awaiting sign-off."""
    _seed_pending_review(
        conn,
        task_id="reflection:r1",
        source_kind="reflection",
        metadata={"proposal": "improve the widget"},
    )
    prompt = _compose(conn)
    assert "== VP COMPLETION REVIEW ==" not in prompt
    assert "reflection:r1" not in prompt


def test_delegation_without_mission_id_does_not_surface(conn):
    """Even with delegation metadata present, an empty/placeholder mission id
    means no real VP mission completed — skip it."""
    _seed_pending_review(
        conn,
        task_id="task:no-mission",
        source_kind="task",
        metadata={"delegation": {"delegate_target": "vp.general.primary"}},
    )
    _seed_pending_review(
        conn,
        task_id="task:placeholder-mission",
        source_kind="task",
        metadata={"delegation": {"mission_id": "?"}},
    )
    prompt = _compose(conn)
    assert "== VP COMPLETION REVIEW ==" not in prompt


def test_real_vp_completion_still_surfaces(conn):
    """A genuine VP completion (delegation.mission_id present) must keep
    surfacing — the allowlist must not silence real sign-off work."""
    _seed_pending_review(
        conn,
        task_id="task:real-vp",
        source_kind="task",
        metadata={
            "delegation": {
                "mission_id": "vp-mission-123",
                "vp_id": "vp.general.primary",
                "vp_terminal_status": "completed",
                "result_summary": "did the thing",
            }
        },
    )
    prompt = _compose(conn)
    assert "== VP COMPLETION REVIEW ==" in prompt
    assert "task:real-vp" in prompt
    assert "vp-mission-123" in prompt


def test_mixed_rows_surface_only_the_real_mission(conn):
    _seed_pending_review(
        conn,
        task_id="reflection:r2",
        source_kind="reflection",
        metadata={},
    )
    _seed_pending_review(
        conn,
        task_id="task:real-vp-2",
        source_kind="task",
        metadata={"delegation": {"mission_id": "vp-mission-456"}},
    )
    prompt = _compose(conn)
    assert "== VP COMPLETION REVIEW ==" in prompt
    assert "1 VP-completed task(s) await your sign-off" in prompt
    assert "task:real-vp-2" in prompt
    assert "reflection:r2" not in prompt
