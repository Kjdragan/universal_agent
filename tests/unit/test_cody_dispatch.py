"""Tests for the Phase 2 task dispatcher (PR 8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_agent import task_hub
from universal_agent.services.cody_dispatch import (
    DEFAULT_WALL_TIME_MAX_MINUTES,
    SOURCE_KIND_CODY_DEMO_TASK,
    _stable_task_id,
    dispatch_cody_demo_task,
    reissue_cody_demo_task_with_feedback,
)
from universal_agent.services.proactive_artifacts import ensure_schema as ensure_artifacts_schema


@pytest.fixture
def conn(tmp_path: Path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    task_hub.ensure_schema(db)
    ensure_artifacts_schema(db)
    yield db
    db.close()


# ── Stable task-id ──────────────────────────────────────────────────────────


def test_task_id_is_stable_across_calls():
    a = _stable_task_id(demo_id="skills__demo-1", entity_slug="skills")
    b = _stable_task_id(demo_id="skills__demo-1", entity_slug="skills")
    assert a == b
    assert a.startswith(SOURCE_KIND_CODY_DEMO_TASK + ":")


def test_task_id_distinguishes_different_demos():
    a = _stable_task_id(demo_id="skills__demo-1", entity_slug="skills")
    b = _stable_task_id(demo_id="skills__demo-2", entity_slug="skills")
    assert a != b


def test_task_id_distinguishes_different_entities():
    a = _stable_task_id(demo_id="demo-1", entity_slug="skills")
    b = _stable_task_id(demo_id="demo-1", entity_slug="memorytool")
    assert a != b


# ── Dispatch ────────────────────────────────────────────────────────────────


def test_dispatch_creates_task_hub_row(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    entity_path = tmp_path / "skills.md"
    entity_path.write_text("# Skills\n", encoding="utf-8")

    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=entity_path,
        demo_id="skills__demo-1",
    )
    assert task["task_id"].startswith(SOURCE_KIND_CODY_DEMO_TASK + ":")
    assert task["source_kind"] == SOURCE_KIND_CODY_DEMO_TASK
    assert task["source_ref"] == "skills__demo-1"
    assert task["status"] == task_hub.TASK_STATUS_OPEN
    assert task["priority"] == 4
    assert task["agent_ready"] is True or task["agent_ready"] == 1


def test_dispatch_metadata_carries_workspace_and_endpoint(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    entity_path = tmp_path / "skills.md"
    entity_path.write_text("# Skills\n", encoding="utf-8")

    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=entity_path,
        demo_id="skills__demo-1",
        endpoint_required="anthropic_native",
        wall_time_max_minutes=45,
    )
    metadata = task.get("metadata") or {}
    assert metadata.get("workspace_dir") == str(workspace)
    assert metadata.get("entity_path") == str(entity_path)
    assert metadata.get("endpoint_required") == "anthropic_native"
    assert int(metadata.get("wall_time_max_minutes") or 0) == 45
    assert metadata.get("queue_policy") == "wait_indefinitely"
    assert metadata.get("preferred_vp") == "vp.coder.primary"


def test_dispatch_default_wall_time(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    metadata = task.get("metadata") or {}
    assert int(metadata.get("wall_time_max_minutes") or 0) == DEFAULT_WALL_TIME_MAX_MINUTES


def test_dispatch_is_idempotent_for_same_demo(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    a = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    b = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    assert a["task_id"] == b["task_id"]


def test_dispatch_distinguishes_iterations(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    a = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
        iteration=1,
    )
    b = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
        iteration=2,
    )
    # Same task_id (idempotent) but iteration in metadata bumps.
    assert a["task_id"] == b["task_id"]
    assert int((b.get("metadata") or {}).get("iteration") or 0) == 2


def test_dispatch_labels_include_endpoint_required(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
        endpoint_required="anthropic_native",
    )
    labels = task.get("labels") or []
    assert "cody-demo" in labels
    assert "v2-phase3" in labels
    assert "endpoint:anthropic_native" in labels


def test_dispatch_extra_metadata_does_not_clobber_required_fields(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
        extra_metadata={"queue_policy": "ATTEMPT_OVERRIDE", "custom": "value"},
    )
    metadata = task.get("metadata") or {}
    # required field NOT clobbered by extra_metadata
    assert metadata.get("queue_policy") == "wait_indefinitely"
    # but custom keys ARE applied
    assert metadata.get("custom") == "value"


# ── Reissue with feedback ───────────────────────────────────────────────────


def test_reissue_with_feedback_carries_feedback_path(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    feedback = workspace / "FEEDBACK.md"
    feedback.write_text("- Cody: please use the Skills decorator.\n", encoding="utf-8")

    task = reissue_cody_demo_task_with_feedback(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
        feedback_path=feedback,
        iteration=2,
    )
    metadata = task.get("metadata") or {}
    assert metadata.get("feedback_path") == str(feedback)
    assert int(metadata.get("iteration") or 0) == 2


def test_reissue_keeps_same_task_id_as_initial_dispatch(conn: sqlite3.Connection, tmp_path: Path):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    initial = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    reissued = reissue_cody_demo_task_with_feedback(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
        feedback_path=workspace / "FEEDBACK.md",
        iteration=2,
    )
    assert initial["task_id"] == reissued["task_id"]
