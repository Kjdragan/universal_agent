from __future__ import annotations

import json
from pathlib import Path

from universal_agent.services.claude_code_intel_rollup import build_rolling_assets


def test_build_rolling_assets_writes_current_artifacts_and_repo_library(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    packet_dir = artifacts_root / "proactive" / "claude_code_intel" / "packets" / "2026-04-23" / "161449__ClaudeDevs"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-04-23T16:14:49.256307+00:00",
                "handle": "ClaudeDevs",
                "new_post_count": 1,
                "action_count": 1,
                "ok": True,
            }
        ),
        encoding="utf-8",
    )
    (packet_dir / "actions.json").write_text(
        json.dumps(
            [
                {
                    "post_id": "123",
                    "tier": 3,
                    "action_type": "demo_task",
                    "url": "https://x.com/ClaudeDevs/status/123",
                    "text": "New Claude Code capability with an official docs link.",
                    "links": ["https://docs.anthropic.com/en/docs/agents-and-tools/mcp"],
                    "classifier": {"reasoning": "This looks directly reusable for agent systems."},
                }
            ]
        ),
        encoding="utf-8",
    )
    linked_root = packet_dir / "linked_sources" / "abc123"
    linked_root.mkdir(parents=True, exist_ok=True)
    (packet_dir / "linked_sources.json").write_text(
        json.dumps(
            [
                {
                    "post_id": "123",
                    "fetch_status": "fetched",
                    "url": "https://docs.anthropic.com/en/docs/agents-and-tools/mcp",
                    "metadata_path": str(linked_root / "metadata.json"),
                }
            ]
        ),
        encoding="utf-8",
    )
    (linked_root / "metadata.json").write_text(
        json.dumps(
            {
                "final_url": "https://docs.anthropic.com/en/docs/agents-and-tools/mcp",
                "domain": "docs.anthropic.com",
                "source_type": "vendor_docs",
                "title": "MCP docs",
                "summary_excerpt": "Official docs for MCP and tool integration.",
            }
        ),
        encoding="utf-8",
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_rollup._repo_root",
        lambda: repo_root,
    )
    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_rollup._has_llm_key",
        lambda: False,
    )

    payload = build_rolling_assets(artifacts_root=artifacts_root)

    report_json = artifacts_root / "proactive" / "claude_code_intel" / "rolling" / "current" / "rolling_14_day_report.json"
    assert report_json.exists()
    saved = json.loads(report_json.read_text(encoding="utf-8"))
    assert saved["window_days"] == 14
    assert saved["bundle_count"] >= 1
    assert "For Kevin" in saved["narrative_markdown"]

    repo_library = repo_root / "agent_capability_library" / "claude_code_intel" / "current"
    assert (repo_library / "rolling_14_day_report.md").exists()
    assert (repo_library / "index.json").exists()
    bundle_dirs = list((repo_library / "bundles").iterdir())
    assert bundle_dirs, "expected at least one synthesized bundle directory"
    assert (bundle_dirs[0] / "bundle.json").exists()
    assert (bundle_dirs[0] / "bundle.md").exists()
    assert (bundle_dirs[0] / "primitives").exists()
    assert payload["repo_outputs"]["bundle_count"] >= 1

