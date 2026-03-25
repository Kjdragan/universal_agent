from __future__ import annotations

import json
from pathlib import Path

from universal_agent.run_workspace import ensure_run_workspace_scaffold


def test_ensure_run_workspace_scaffold_creates_manifest_activity_and_attempt(tmp_path: Path):
    workspace = tmp_path / "run_alpha"

    result = ensure_run_workspace_scaffold(
        workspace_dir=workspace,
        run_id="run_alpha",
        attempt_id="run_alpha:attempt:1",
        attempt_number=1,
        status="queued",
        run_kind="unit_test",
        trigger_source="unit",
    )

    manifest_path = workspace / "run_manifest.json"
    activity_path = workspace / "activity.jsonl"
    attempt_meta_path = workspace / "attempts" / "001" / "attempt_meta.json"

    assert Path(result["manifest_path"]) == manifest_path
    assert manifest_path.exists()
    assert activity_path.exists()
    assert attempt_meta_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run_alpha"
    assert manifest["latest_attempt_number"] == 1
    assert manifest["run_kind"] == "unit_test"
    assert manifest["trigger_source"] == "unit"

    attempt_meta = json.loads(attempt_meta_path.read_text(encoding="utf-8"))
    assert attempt_meta["attempt_number"] == 1
    assert attempt_meta["status"] == "queued"


def test_ensure_run_workspace_scaffold_snapshots_terminal_attempt_evidence(tmp_path: Path):
    workspace = tmp_path / "run_beta"
    workspace.mkdir()
    (workspace / "run.log").write_text("log-line\n", encoding="utf-8")
    (workspace / "trace.json").write_text('{"trace": true}\n', encoding="utf-8")
    (workspace / "transcript.md").write_text("# transcript\n", encoding="utf-8")
    (workspace / "run_checkpoint.json").write_text('{"checkpoint": true}\n', encoding="utf-8")
    (workspace / "work_products").mkdir()
    (workspace / "work_products" / "result.txt").write_text("done\n", encoding="utf-8")

    ensure_run_workspace_scaffold(
        workspace_dir=workspace,
        run_id="run_beta",
        attempt_id="run_beta:attempt:1",
        attempt_number=1,
        status="completed",
        run_kind="unit_test",
        trigger_source="unit",
    )

    manifest = json.loads((workspace / "run_manifest.json").read_text(encoding="utf-8"))
    attempt_dir = workspace / "attempts" / "001"
    attempt_meta = json.loads((attempt_dir / "attempt_meta.json").read_text(encoding="utf-8"))

    assert manifest["canonical_attempt_id"] == "run_beta:attempt:1"
    assert manifest["canonical_attempt_number"] == 1
    assert (attempt_dir / "run.log").read_text(encoding="utf-8") == "log-line\n"
    assert json.loads((attempt_dir / "trace.json").read_text(encoding="utf-8")) == {"trace": True}
    assert (attempt_dir / "transcript.md").read_text(encoding="utf-8") == "# transcript\n"
    assert json.loads((attempt_dir / "run_checkpoint.json").read_text(encoding="utf-8")) == {
        "checkpoint": True
    }
    assert (attempt_dir / "work_products" / "result.txt").read_text(encoding="utf-8") == "done\n"
    assert attempt_meta["artifact_snapshot_at"]
    assert "run.log" in attempt_meta["copied_files"]
    assert "work_products" in attempt_meta["copied_dirs"]
