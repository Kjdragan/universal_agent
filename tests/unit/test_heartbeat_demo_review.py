"""Tests for the Phase-4 Cody demo-review prompt injection in the heartbeat.

Regression coverage for the gap where a cody_demo_task in pending_review was
swept into the VP COMPLETION REVIEW block (mislabeled "delegated to
vp.coder.primary") and never routed to the cody-work-evaluator lane, so Simone
never ran the review.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.heartbeat_service import _compose_heartbeat_prompt
from universal_agent.services.cody_dispatch import dispatch_cody_demo_task
from universal_agent.services.cody_implementation import DemoManifest, write_manifest
from universal_agent.services.proactive_artifacts import (
    ensure_schema as ensure_artifacts_schema,
)


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    task_hub.ensure_schema(db)
    ensure_artifacts_schema(db)
    yield db
    db.close()


def _pending_review_demo(conn, tmp_path: Path) -> dict:
    workspace = tmp_path / "code-review__demo-1"
    workspace.mkdir(parents=True, exist_ok=True)
    write_manifest(
        workspace,
        DemoManifest(
            demo_id="code-review__demo-1",
            feature="code-review",
            endpoint_required="anthropic_native",
            endpoint_hit="anthropic_native",
            acceptance_passed=False,
            iteration=1,
        ),
    )
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="code-review",
        entity_path=tmp_path / "code-review.md",
        demo_id="code-review__demo-1",
    )
    # Simulate the VP worker terminal handler routing it to pending_review,
    # including the delegation metadata that previously mislabeled it.
    conn.execute(
        "UPDATE task_hub_items SET status=?, "
        "metadata_json=json_set(metadata_json, '$.delegation', "
        "json('{\"delegate_reason\": \"Delegated to vp.coder.primary\"}')) "
        "WHERE task_id=?",
        (task_hub.TASK_STATUS_PENDING_REVIEW, task["task_id"]),
    )
    conn.commit()
    return task


def test_pending_review_demo_surfaces_in_dedicated_block(conn, tmp_path: Path):
    task = _pending_review_demo(conn, tmp_path)
    prompt = _compose_heartbeat_prompt(
        "BASE",
        investigation_only=False,
        task_hub_claims=[],
        runtime_conn=conn,
    )
    assert "== CODY DEMO REVIEW (Phase 4) ==" in prompt
    assert task["task_id"] in prompt
    assert "cody-work-evaluator" in prompt
    # Readiness signal is surfaced explicitly.
    assert "manifest_present=True" in prompt


def test_pending_review_demo_not_framed_as_vp_mission(conn, tmp_path: Path):
    """The demo must NOT appear inside the VP COMPLETION REVIEW block."""
    task = _pending_review_demo(conn, tmp_path)
    prompt = _compose_heartbeat_prompt(
        "BASE",
        investigation_only=False,
        task_hub_claims=[],
        runtime_conn=conn,
    )
    # Locate the two review sections. The demo task_id must fall in the demo
    # block, and the VP block must not exist (no other pending_review rows).
    assert "== VP COMPLETION REVIEW ==" not in prompt
    demo_idx = prompt.index("== CODY DEMO REVIEW (Phase 4) ==")
    assert prompt.index(task["task_id"]) > demo_idx


def test_no_demo_block_when_none_pending(conn, tmp_path: Path):
    # An open (not pending_review) demo must not trigger the review block.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    prompt = _compose_heartbeat_prompt(
        "BASE",
        investigation_only=False,
        task_hub_claims=[],
        runtime_conn=conn,
    )
    assert "== CODY DEMO REVIEW (Phase 4) ==" not in prompt


def test_self_opens_connection_when_runtime_conn_none(tmp_path: Path, monkeypatch):
    """Regression for the production deadness: the heartbeat passed runtime_conn=None
    (the gateway has no get_db_conn), so the review block never rendered. The
    composer must open its OWN connection and still surface the demo."""
    from universal_agent.durable.db import connect_runtime_db
    import universal_agent.heartbeat_service as hs

    db_path = tmp_path / "activity.db"
    seed = connect_runtime_db(str(db_path))
    seed.row_factory = sqlite3.Row
    task_hub.ensure_schema(seed)
    ensure_artifacts_schema(seed)
    task = _pending_review_demo(seed, tmp_path)
    seed.commit()
    seed.close()

    # Point the composer's self-open path at our temp activity DB.
    monkeypatch.setattr(hs, "get_activity_db_path", lambda: str(db_path))

    prompt = _compose_heartbeat_prompt(
        "BASE",
        investigation_only=False,
        task_hub_claims=[],
        runtime_conn=None,  # exactly the production condition
    )
    assert "== CODY DEMO REVIEW (Phase 4) ==" in prompt
    assert task["task_id"] in prompt
    assert "manifest_present=True" in prompt
