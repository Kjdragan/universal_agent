"""Tests for Phase 4 Simone evaluation helpers (PR 10)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services.cody_dispatch import (
    SOURCE_KIND_CODY_DEMO_TASK,
    dispatch_cody_demo_task,
)
from universal_agent.services.cody_evaluation import (
    VERDICT_DEFER,
    VERDICT_ITERATE,
    VERDICT_PASS,
    attach_demo_to_vault_entity,
    complete_demo_task,
    defer_demo_task,
    detach_demo_from_vault_entity,
    evaluate_demo,
    finalize_direct_demo,
    monitor_demo_tasks,
    write_feedback_file,
)
from universal_agent.services.cody_implementation import (
    DemoManifest,
    write_manifest,
)
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


def _populate_workspace(
    workspace: Path,
    *,
    demo_id: str = "skills__demo-1",
    feature: str = "skills",
    endpoint_required: str = "anthropic_native",
    endpoint_hit: str = "anthropic_native",
    acceptance_passed: bool = True,
) -> DemoManifest:
    """Populate a complete demo workspace for evaluator tests."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "BRIEF.md").write_text("# Skills brief\nReal content.\n", encoding="utf-8")
    (workspace / "ACCEPTANCE.md").write_text("1. demo MUST OK\n", encoding="utf-8")
    (workspace / "business_relevance.md").write_text("client value text\n", encoding="utf-8")
    (workspace / "SOURCES").mkdir(exist_ok=True)
    (workspace / "run_output.txt").write_text("OK from demo\n", encoding="utf-8")

    manifest = DemoManifest(
        demo_id=demo_id,
        feature=feature,
        endpoint_required=endpoint_required,
        endpoint_hit=endpoint_hit,
        model_used="claude-haiku-4-5-20251001",
        wall_time_seconds=12.3,
        acceptance_passed=acceptance_passed,
        iteration=1,
    )
    write_manifest(workspace, manifest)
    return manifest


# ── monitor_demo_tasks ──────────────────────────────────────────────────────


def test_monitor_demo_tasks_returns_only_cody_demo_rows(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    # Add a non-cody-demo row to make sure it's filtered out.
    task_hub.upsert_item(
        conn,
        {
            "task_id": "other:abc",
            "source_kind": "claude_code_kb_update",
            "source_ref": "post_1",
            "title": "Unrelated",
            "status": task_hub.TASK_STATUS_OPEN,
        },
    )
    rows = monitor_demo_tasks(conn)
    assert len(rows) == 1
    assert rows[0]["demo_id"] == "skills__demo-1"
    assert rows[0]["entity_slug"] == "skills"


def test_monitor_demo_tasks_returns_empty_when_no_cody_tasks(conn):
    assert monitor_demo_tasks(conn) == []


def test_monitor_demo_tasks_carries_iteration_count(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
        iteration=3,
    )
    rows = monitor_demo_tasks(conn)
    assert rows[0]["iteration"] == 3


def _set_status(conn, task_id: str, status: str) -> None:
    conn.execute(
        "UPDATE task_hub_items SET status=? WHERE task_id=?", (status, task_id)
    )
    conn.commit()


def test_monitor_demo_tasks_marks_ready_for_review_with_manifest(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace)  # writes a root manifest.json
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    _set_status(conn, task["task_id"], task_hub.TASK_STATUS_PENDING_REVIEW)
    row = monitor_demo_tasks(conn)[0]
    assert row["status"] == "pending_review"
    assert row["manifest_present"] is True
    assert row["ready_for_review"] is True
    assert row["state"] == "ready_for_review"


def test_monitor_demo_tasks_pending_review_without_manifest_not_ready(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()  # no manifest written
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    _set_status(conn, task["task_id"], task_hub.TASK_STATUS_PENDING_REVIEW)
    row = monitor_demo_tasks(conn)[0]
    assert row["manifest_present"] is False
    assert row["ready_for_review"] is False
    assert row["state"] == "pending_review_no_manifest"


def test_monitor_demo_tasks_state_building_when_delegated(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    _set_status(conn, task["task_id"], task_hub.TASK_STATUS_DELEGATED)
    row = monitor_demo_tasks(conn)[0]
    assert row["ready_for_review"] is False
    assert row["state"] == "building"


def test_monitor_demo_tasks_resolves_vp_mission_manifest(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Curated build: manifest lands in a vp-mission subdir, not the root.
    sub = workspace / "vp-mission-abc123"
    _populate_workspace(sub)
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    _set_status(conn, task["task_id"], task_hub.TASK_STATUS_PENDING_REVIEW)
    row = monitor_demo_tasks(conn)[0]
    assert row["manifest_present"] is True
    assert row["ready_for_review"] is True


def test_evaluate_demo_reads_vp_mission_subdir_artifacts(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Phase-2 scaffold files stay at root...
    (workspace / "BRIEF.md").write_text("# brief\nreal\n", encoding="utf-8")
    (workspace / "ACCEPTANCE.md").write_text("1. MUST ok\n", encoding="utf-8")
    (workspace / "business_relevance.md").write_text("value\n", encoding="utf-8")
    # ...but the VP-built artifacts land in a mission subdir.
    sub = workspace / "vp-mission-abc123"
    sub.mkdir()
    (sub / "BUILD_NOTES.md").write_text("notes from the mission subdir\n", encoding="utf-8")
    (sub / "run_output.txt").write_text("RAN in subdir\n", encoding="utf-8")
    write_manifest(
        sub,
        DemoManifest(
            demo_id="skills__demo-1",
            feature="skills",
            endpoint_required="anthropic_native",
            endpoint_hit="anthropic_native",
            acceptance_passed=True,
            iteration=1,
        ),
    )
    report = evaluate_demo(workspace, demo_id="skills__demo-1", entity_slug="skills")
    assert report.manifest_present is True
    assert "subdir" in report.build_notes_excerpt
    assert "RAN in subdir" in report.run_output_excerpt


# ── evaluate_demo ───────────────────────────────────────────────────────────


def test_evaluate_demo_complete_workspace_passes_all_checks(tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace)
    report = evaluate_demo(workspace, demo_id="skills__demo-1", entity_slug="skills")
    assert report.workspace_complete.ok is True
    assert report.cody_self_reported_pass.ok is True
    assert report.endpoint_match.ok is True
    assert report.overall_mechanical_ok is True
    assert report.manifest is not None
    assert report.iteration == 1


def test_evaluate_demo_flags_endpoint_mismatch(tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace, endpoint_hit="zai")
    report = evaluate_demo(workspace, demo_id="skills__demo-1")
    assert report.endpoint_match.ok is False
    assert "env-leak" in report.endpoint_match.detail or "mismatch" in report.endpoint_match.detail
    assert report.overall_mechanical_ok is False


def test_evaluate_demo_endpoint_required_any_passes_anything(tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace, endpoint_required="any", endpoint_hit="zai")
    report = evaluate_demo(workspace)
    assert report.endpoint_match.ok is True


def test_evaluate_demo_accepts_raw_anthropic_host(tmp_path: Path):
    """Regression: Cody free-hands the raw host 'api.anthropic.com' into the
    manifest; it IS the native endpoint and must satisfy required=anthropic_native
    instead of false-rejecting (the historical P3 conversion bug)."""
    workspace = tmp_path / "ws"
    _populate_workspace(
        workspace, endpoint_required="anthropic_native", endpoint_hit="api.anthropic.com"
    )
    report = evaluate_demo(workspace, demo_id="hooks__demo-1")
    assert report.endpoint_match.ok is True
    assert report.overall_mechanical_ok is True


def test_evaluate_demo_still_flags_real_zai_leak_against_anthropic(tmp_path: Path):
    """The normalizer must not paper over a genuine env-leak: a ZAI host hit
    against a required anthropic_native is still a mismatch."""
    workspace = tmp_path / "ws"
    _populate_workspace(
        workspace, endpoint_required="anthropic_native", endpoint_hit="api.z.ai"
    )
    report = evaluate_demo(workspace, demo_id="hooks__demo-1")
    assert report.endpoint_match.ok is False
    assert report.overall_mechanical_ok is False


def test_evaluate_demo_flags_cody_self_reported_failure(tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace, acceptance_passed=False)
    report = evaluate_demo(workspace)
    assert report.cody_self_reported_pass.ok is False
    assert report.overall_mechanical_ok is False


def test_evaluate_demo_handles_missing_manifest(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "BRIEF.md").write_text("brief\n", encoding="utf-8")
    (workspace / "ACCEPTANCE.md").write_text("acceptance\n", encoding="utf-8")
    (workspace / "business_relevance.md").write_text("biz\n", encoding="utf-8")
    report = evaluate_demo(workspace, demo_id="x", entity_slug="x")
    assert report.manifest_present is False
    assert report.endpoint_match.ok is False
    assert "no manifest" in report.endpoint_match.detail


def test_evaluate_demo_flags_incomplete_workspace(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Only BRIEF, no ACCEPTANCE / business_relevance.
    (workspace / "BRIEF.md").write_text("brief\n", encoding="utf-8")
    report = evaluate_demo(workspace)
    assert report.workspace_complete.ok is False
    assert report.briefing_present is False


def test_evaluate_demo_includes_build_notes_excerpt(tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace)
    (workspace / "BUILD_NOTES.md").write_text(
        "# Notes\n\n## [2026-05-05] GAP\n\nCouldn't find SkillRegistry init.\n",
        encoding="utf-8",
    )
    report = evaluate_demo(workspace)
    assert "SkillRegistry" in report.build_notes_excerpt


def test_evaluate_demo_includes_run_output_excerpt(tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace)
    report = evaluate_demo(workspace)
    assert "OK from demo" in report.run_output_excerpt


def test_evaluate_demo_with_rerun_command(tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace)
    report = evaluate_demo(
        workspace,
        rerun_command=["python3", "-c", "print('rerun output: model claude-haiku-4-5')"],
        rerun_timeout=15,
    )
    assert report.rerun is not None
    assert report.rerun.get("ok") is True
    assert "claude-haiku" in report.rerun.get("stdout_excerpt", "")
    assert report.rerun.get("detected_endpoint") == "anthropic_native"


# ── write_feedback_file ─────────────────────────────────────────────────────


def test_write_feedback_file_creates_with_iteration_header(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = write_feedback_file(
        workspace,
        feedback_markdown="- Cody: please use SkillRegistry.register()",
        iteration=2,
    )
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "iteration 2" in text
    assert "SkillRegistry.register()" in text


def test_write_feedback_file_overwrites_previous(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_feedback_file(workspace, feedback_markdown="first round", iteration=2)
    write_feedback_file(workspace, feedback_markdown="second round", iteration=3)
    text = (workspace / "FEEDBACK.md").read_text(encoding="utf-8")
    assert "second round" in text
    assert "first round" not in text


# ── complete_demo_task & defer_demo_task ────────────────────────────────────


def test_complete_demo_task_marks_completed(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    updated = complete_demo_task(
        conn,
        task_id=task["task_id"],
        completion_summary="Skills demo passes.",
    )
    assert updated["status"] == task_hub.TASK_STATUS_COMPLETED
    assert (updated.get("metadata") or {}).get("completion_summary") == "Skills demo passes."
    # agent_ready must be False so the dispatcher doesn't re-pick it up.
    assert not updated.get("agent_ready")


def test_complete_demo_task_raises_for_missing_task_id(conn):
    with pytest.raises(KeyError):
        complete_demo_task(conn, task_id="never_existed:abc")


def test_defer_demo_task_parks_with_reason(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn,
        workspace_dir=workspace,
        entity_slug="skills",
        entity_path=tmp_path / "skills.md",
        demo_id="skills__demo-1",
    )
    updated = defer_demo_task(
        conn,
        task_id=task["task_id"],
        reason="Docs too thin for faithful implementation.",
    )
    assert updated["status"] == task_hub.TASK_STATUS_PARKED
    assert (updated.get("metadata") or {}).get("deferred_reason") == "Docs too thin for faithful implementation."
    assert not updated.get("agent_ready")


def test_defer_demo_task_raises_for_missing_task_id(conn):
    with pytest.raises(KeyError):
        defer_demo_task(conn, task_id="never_existed:abc", reason="x")


# ── direct/ungated demo lane (review_required=False) ────────────────────────


def test_dispatch_records_review_required_flag(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn, workspace_dir=workspace, entity_slug="d", entity_path=tmp_path / "d.md",
        demo_id="d__direct", review_required=False,
    )
    assert (task.get("metadata") or {}).get("review_required") is False


def test_dispatch_review_required_defaults_true(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = dispatch_cody_demo_task(
        conn, workspace_dir=workspace, entity_slug="d", entity_path=tmp_path / "d.md",
        demo_id="d__curated",
    )
    assert (task.get("metadata") or {}).get("review_required") is True


def test_finalize_direct_demo_completes_on_anthropic_match(conn, tmp_path: Path):
    workspace = tmp_path / "ws"
    _populate_workspace(workspace, demo_id="a__direct", endpoint_hit="api.anthropic.com")
    task = dispatch_cody_demo_task(
        conn, workspace_dir=workspace, entity_slug="a", entity_path=workspace / "BRIEF.md",
        demo_id="a__direct", endpoint_required="anthropic_native", review_required=False,
    )
    res = finalize_direct_demo(conn, task_id=task["task_id"])
    assert res["status"] == "completed"
    assert res["endpoint_ok"] is True
    assert task_hub.get_item(conn, task["task_id"])["status"] == task_hub.TASK_STATUS_COMPLETED


def test_finalize_direct_demo_completes_on_zai_match(conn, tmp_path: Path):
    # ZAI demo: task requires 'zai', manifest hit 'api.z.ai' → canonical match → done.
    workspace = tmp_path / "ws"
    _populate_workspace(
        workspace, demo_id="z__direct", endpoint_required="zai", endpoint_hit="api.z.ai"
    )
    task = dispatch_cody_demo_task(
        conn, workspace_dir=workspace, entity_slug="z", entity_path=workspace / "BRIEF.md",
        demo_id="z__direct", endpoint_required="zai", review_required=False,
    )
    res = finalize_direct_demo(conn, task_id=task["task_id"])
    assert res["status"] == "completed"
    assert task_hub.get_item(conn, task["task_id"])["status"] == task_hub.TASK_STATUS_COMPLETED


def test_finalize_direct_demo_flags_endpoint_miss(conn, tmp_path: Path):
    # Required anthropic_native but ran on ZAI → needs_attention, NOT completed.
    workspace = tmp_path / "ws"
    _populate_workspace(
        workspace, demo_id="m__direct", endpoint_required="anthropic_native", endpoint_hit="api.z.ai"
    )
    task = dispatch_cody_demo_task(
        conn, workspace_dir=workspace, entity_slug="m", entity_path=workspace / "BRIEF.md",
        demo_id="m__direct", endpoint_required="anthropic_native", review_required=False,
    )
    res = finalize_direct_demo(conn, task_id=task["task_id"])
    assert res["status"] == "needs_attention"
    assert res["endpoint_ok"] is False
    assert task_hub.get_item(conn, task["task_id"])["status"] != task_hub.TASK_STATUS_COMPLETED


# ── attach_demo_to_vault_entity ─────────────────────────────────────────────


def _write_entity_page(vault_root: Path, slug: str, body: str = "# Skills\n\nFeature description.\n") -> Path:
    entities = vault_root / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    path = entities / f"{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_attach_demo_creates_demos_section_when_missing(tmp_path: Path):
    vault = tmp_path / "vault"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_entity_page(vault, "skills")
    manifest = DemoManifest(
        demo_id="skills__demo-1",
        feature="skills",
        endpoint_required="anthropic_native",
        endpoint_hit="anthropic_native",
    )
    target = attach_demo_to_vault_entity(
        workspace_dir=workspace,
        vault_root=vault,
        entity_slug="skills",
        manifest=manifest,
    )
    text = target.read_text(encoding="utf-8")
    assert "## Demos" in text
    assert "skills__demo-1" in text
    assert "endpoint: `anthropic_native`" in text


def test_attach_demo_appends_to_existing_demos_section(tmp_path: Path):
    vault = tmp_path / "vault"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    body = "# Skills\n\nDescription.\n\n## Demos\n\n- existing-demo — old line\n"
    _write_entity_page(vault, "skills", body=body)
    manifest = DemoManifest(
        demo_id="skills__demo-2",
        feature="skills",
        endpoint_required="anthropic_native",
        endpoint_hit="anthropic_native",
    )
    target = attach_demo_to_vault_entity(
        workspace_dir=workspace,
        vault_root=vault,
        entity_slug="skills",
        manifest=manifest,
    )
    text = target.read_text(encoding="utf-8")
    # Both bullets present.
    assert "existing-demo" in text
    assert "skills__demo-2" in text
    # Only one ## Demos header.
    assert text.count("## Demos") == 1


def test_attach_demo_raises_for_missing_entity(tmp_path: Path):
    vault = tmp_path / "vault"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(FileNotFoundError):
        attach_demo_to_vault_entity(
            workspace_dir=workspace,
            vault_root=vault,
            entity_slug="nonexistent",
        )


def test_attach_demo_works_without_manifest(tmp_path: Path):
    vault = tmp_path / "vault"
    workspace = tmp_path / "ws-without-manifest"
    workspace.mkdir()
    _write_entity_page(vault, "skills")
    target = attach_demo_to_vault_entity(
        workspace_dir=workspace,
        vault_root=vault,
        entity_slug="skills",
        manifest=None,
    )
    text = target.read_text(encoding="utf-8")
    # demo_id falls back to workspace dir name.
    assert "ws-without-manifest" in text


# ── detach_demo_from_vault_entity ───────────────────────────────────────────


def test_detach_demo_removes_matching_bullet(tmp_path: Path):
    vault = tmp_path / "vault"
    body = (
        "# Skills\n\nDescription.\n\n"
        "## Demos\n\n"
        "- `skills__demo-1` — `/path/1` — endpoint: `anthropic_native` — attached t1\n"
        "- `skills__demo-2` — `/path/2` — endpoint: `anthropic_native` — attached t2\n"
    )
    _write_entity_page(vault, "skills", body=body)
    target = detach_demo_from_vault_entity(vault_root=vault, entity_slug="skills", demo_id="skills__demo-1")
    text = target.read_text(encoding="utf-8")
    assert "skills__demo-1" not in text
    assert "skills__demo-2" in text


def test_detach_demo_is_idempotent_for_missing(tmp_path: Path):
    vault = tmp_path / "vault"
    body = "# Skills\n\nDescription.\n\n## Demos\n\n- `skills__demo-2` — line\n"
    _write_entity_page(vault, "skills", body=body)
    # Detach a demo_id that isn't there → no error, file unchanged in substance.
    target = detach_demo_from_vault_entity(vault_root=vault, entity_slug="skills", demo_id="skills__demo-1")
    assert "skills__demo-2" in target.read_text(encoding="utf-8")


def test_detach_demo_handles_entity_without_demos_section(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity_page(vault, "skills")
    # Should be a no-op (no Demos section to mutate).
    target = detach_demo_from_vault_entity(vault_root=vault, entity_slug="skills", demo_id="anything")
    assert target.exists()


def test_verdict_constants_are_exported():
    """Sanity check: verdict labels are available as constants for skill code."""
    assert VERDICT_PASS == "pass"
    assert VERDICT_ITERATE == "iterate"
    assert VERDICT_DEFER == "defer"
