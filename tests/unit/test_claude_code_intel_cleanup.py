from __future__ import annotations

import json
from pathlib import Path

from universal_agent.services.claude_code_intel_cleanup import cleanup_historical_cron_workspace


def _seed_workspace(root: Path) -> Path:
    workspace = root / "cron_claude_code_intel_sync"
    (workspace / "work_products").mkdir(parents=True, exist_ok=True)
    (workspace / "heartbeat_state.json").write_text("{}", encoding="utf-8")
    (workspace / "work_products" / "heartbeat_state.json").write_text("{}", encoding="utf-8")
    (workspace / "work_products" / "heartbeat_findings_latest.json").write_text("{}", encoding="utf-8")
    (workspace / "work_products" / "system_health_latest.md").write_text("# health\n", encoding="utf-8")
    (workspace / "transcript.md").write_text("mixed transcript\n", encoding="utf-8")
    return workspace


def test_cleanup_workspace_dry_run_reports_pollution(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)

    result = cleanup_historical_cron_workspace(workspace_dir=workspace, dry_run=True)

    assert result.polluted is True
    assert sorted(result.archived_paths) == sorted(
        [
            "heartbeat_state.json",
            "work_products/heartbeat_state.json",
            "work_products/heartbeat_findings_latest.json",
            "work_products/system_health_latest.md",
        ]
    )
    assert (workspace / "heartbeat_state.json").exists()
    assert (workspace / "work_products" / "heartbeat_findings_latest.json").exists()
    assert (workspace / "transcript.md").exists()


def test_cleanup_workspace_apply_moves_heartbeat_artifacts_and_keeps_transcript(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)

    result = cleanup_historical_cron_workspace(workspace_dir=workspace, dry_run=False)

    cleanup_dir = Path(result.cleanup_dir)
    assert result.polluted is True
    assert not (workspace / "heartbeat_state.json").exists()
    assert not (workspace / "work_products" / "heartbeat_state.json").exists()
    assert not (workspace / "work_products" / "heartbeat_findings_latest.json").exists()
    assert not (workspace / "work_products" / "system_health_latest.md").exists()
    assert (workspace / "transcript.md").exists()
    assert (cleanup_dir / "heartbeat_state.json").exists()
    assert (cleanup_dir / "work_products" / "heartbeat_state.json").exists()
    assert (cleanup_dir / "work_products" / "heartbeat_findings_latest.json").exists()
    assert (cleanup_dir / "work_products" / "system_health_latest.md").exists()
    manifest = json.loads((cleanup_dir / "cleanup_manifest.json").read_text(encoding="utf-8"))
    assert manifest["workspace_dir"] == str(workspace)
    note = (cleanup_dir / "README.md").read_text(encoding="utf-8")
    assert "transcript.md" in note


def test_cleanup_workspace_reports_clean_when_no_heartbeat_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "cron_claude_code_intel_sync"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "transcript.md").write_text("clean transcript\n", encoding="utf-8")

    result = cleanup_historical_cron_workspace(workspace_dir=workspace, dry_run=False)

    assert result.polluted is False
    assert result.archived_paths == []
    assert (workspace / "transcript.md").exists()
