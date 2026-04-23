from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from universal_agent.services.claude_code_intel_rollup import build_rolling_assets


def _make_packet_dir(artifacts_root: Path, *, date: str = "2026-04-23", stamp: str = "161449") -> Path:
    packet_dir = artifacts_root / "proactive" / "claude_code_intel" / "packets" / date / f"{stamp}__ClaudeDevs"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": f"{date}T16:14:49.256307+00:00",
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
    return packet_dir


def test_build_rolling_assets_writes_current_artifacts_and_repo_library(monkeypatch, tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    _make_packet_dir(artifacts_root)

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


def test_rolling_output_includes_synthesis_method(monkeypatch, tmp_path: Path) -> None:
    """The rolling JSON should indicate whether LLM or fallback synthesis was used."""
    artifacts_root = tmp_path / "artifacts"
    _make_packet_dir(artifacts_root)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._repo_root", lambda: repo_root)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._has_llm_key", lambda: False)

    payload = build_rolling_assets(artifacts_root=artifacts_root)

    report_json = artifacts_root / "proactive" / "claude_code_intel" / "rolling" / "current" / "rolling_14_day_report.json"
    saved = json.loads(report_json.read_text(encoding="utf-8"))
    # Must contain synthesis_method field
    assert "synthesis_method" in saved, "rolling JSON must include synthesis_method"
    assert saved["synthesis_method"] in ("llm", "fallback"), f"synthesis_method must be 'llm' or 'fallback', got {saved['synthesis_method']}"
    # For this test with no LLM key, must be fallback
    assert saved["synthesis_method"] == "fallback"


def test_rolling_output_always_includes_generated_at(monkeypatch, tmp_path: Path) -> None:
    """generated_at must always be present and non-empty in the rolling JSON."""
    artifacts_root = tmp_path / "artifacts"
    _make_packet_dir(artifacts_root)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._repo_root", lambda: repo_root)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._has_llm_key", lambda: False)

    build_rolling_assets(artifacts_root=artifacts_root)

    report_json = artifacts_root / "proactive" / "claude_code_intel" / "rolling" / "current" / "rolling_14_day_report.json"
    saved = json.loads(report_json.read_text(encoding="utf-8"))
    assert saved.get("generated_at"), "generated_at must be present and non-empty in rolling JSON"
    # Verify it's a parseable ISO date
    from datetime import datetime
    datetime.fromisoformat(saved["generated_at"].replace("Z", "+00:00"))


def test_llm_synthesis_failure_logs_warning(monkeypatch, tmp_path: Path, caplog) -> None:
    """When LLM synthesis fails, a warning must be logged with the exception details."""
    artifacts_root = tmp_path / "artifacts"
    _make_packet_dir(artifacts_root)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._repo_root", lambda: repo_root)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._has_llm_key", lambda: True)

    def _raise(*args, **kwargs):
        raise RuntimeError("test_api_failure")

    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._call_sync_llm", _raise)

    with caplog.at_level(logging.WARNING, logger="universal_agent.services.claude_code_intel_rollup"):
        payload = build_rolling_assets(artifacts_root=artifacts_root)

    # Must still produce output (fallback)
    assert payload["bundle_count"] >= 1
    # Must have logged a warning about the failure
    assert any("test_api_failure" in record.message for record in caplog.records), \
        "Expected a warning log mentioning the LLM failure reason"


def test_fallback_bundle_includes_linked_source_context(monkeypatch, tmp_path: Path) -> None:
    """Fallback bundles should incorporate linked source titles and summaries,
    not just generic placeholder text."""
    artifacts_root = tmp_path / "artifacts"
    _make_packet_dir(artifacts_root)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._repo_root", lambda: repo_root)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._has_llm_key", lambda: False)

    payload = build_rolling_assets(artifacts_root=artifacts_root)

    bundles = payload.get("bundles") or []
    assert bundles, "expected at least one bundle"
    bundle = bundles[0]
    # The for_kevin_markdown should reference the specific linked source, not just generic text
    kevin = str(bundle.get("for_kevin_markdown") or "")
    assert "MCP" in kevin or "docs.anthropic.com" in kevin, \
        f"for_kevin_markdown should reference the specific linked source content, got: {kevin[:200]}"


def _make_packet_with_actions(
    artifacts_root: Path,
    *,
    handle: str = "ClaudeDevs",
    date: str = "2026-04-23",
    stamp: str = "161449",
    actions: list[dict],
) -> Path:
    """Create a packet with arbitrary action tiers and handles."""
    packet_dir = artifacts_root / "proactive" / "claude_code_intel" / "packets" / date / f"{stamp}__{handle}"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "manifest.json").write_text(
        json.dumps({
            "generated_at": f"{date}T16:14:49.256307+00:00",
            "handle": handle,
            "new_post_count": len(actions),
            "action_count": len(actions),
            "ok": True,
        }),
        encoding="utf-8",
    )
    (packet_dir / "actions.json").write_text(json.dumps(actions), encoding="utf-8")
    (packet_dir / "linked_sources.json").write_text("[]", encoding="utf-8")
    return packet_dir


def test_rollup_excludes_tier1_from_synthesis(monkeypatch, tmp_path: Path) -> None:
    """Tier 1 digest posts should stay in packets but never appear as capability bundles."""
    artifacts_root = tmp_path / "artifacts"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._repo_root", lambda: repo_root)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._has_llm_key", lambda: False)

    _make_packet_with_actions(
        artifacts_root,
        handle="bcherny",
        date="2026-04-23",
        stamp="100000",
        actions=[
            {
                "post_id": "tier1_personal",
                "tier": 1,
                "action_type": "digest",
                "url": "https://x.com/bcherny/status/tier1_personal",
                "text": "Great dinner with the team!",
                "links": [],
                "classifier": {"reasoning": "Personal tweet, no technical content."},
            },
        ],
    )
    _make_packet_with_actions(
        artifacts_root,
        handle="ClaudeDevs",
        date="2026-04-23",
        stamp="100001",
        actions=[
            {
                "post_id": "tier3_feature",
                "tier": 3,
                "action_type": "demo_task",
                "url": "https://x.com/ClaudeDevs/status/tier3_feature",
                "text": "New MCP integration available for Claude Code.",
                "links": ["https://docs.anthropic.com/mcp"],
                "classifier": {"reasoning": "Concrete feature with official docs link."},
            },
        ],
    )

    payload = build_rolling_assets(artifacts_root=artifacts_root)

    bundles = payload.get("bundles") or []
    bundle_post_ids = set()
    for bundle in bundles:
        for post_id in bundle.get("discovery_posts") or []:
            bundle_post_ids.add(post_id)

    # Tier 3 should be present
    assert "tier3_feature" in bundle_post_ids, "Tier 3 post should produce a bundle"
    # Tier 1 should NOT be present
    assert "tier1_personal" not in bundle_post_ids, "Tier 1 digest should NOT produce a bundle"


def test_rollup_includes_tier2_from_any_handle(monkeypatch, tmp_path: Path) -> None:
    """Tier 2+ posts from any handle should flow into synthesis."""
    artifacts_root = tmp_path / "artifacts"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._repo_root", lambda: repo_root)
    monkeypatch.setattr("universal_agent.services.claude_code_intel_rollup._has_llm_key", lambda: False)

    _make_packet_with_actions(
        artifacts_root,
        handle="bcherny",
        date="2026-04-23",
        stamp="100010",
        actions=[
            {
                "post_id": "boris_tier2_docs",
                "tier": 2,
                "action_type": "kb_update",
                "url": "https://x.com/bcherny/status/boris_tier2_docs",
                "text": "Updated docs on how Claude Code handles prompt caching.",
                "links": ["https://docs.anthropic.com/prompt-caching"],
                "classifier": {"reasoning": "Docs reference from Claude Code creator."},
            },
        ],
    )

    payload = build_rolling_assets(artifacts_root=artifacts_root)

    bundles = payload.get("bundles") or []
    assert bundles, "Tier 2 post from bcherny should produce a bundle"
    bundle_post_ids = set()
    for bundle in bundles:
        for post_id in bundle.get("discovery_posts") or []:
            bundle_post_ids.add(post_id)
    assert "boris_tier2_docs" in bundle_post_ids, "Tier 2 from bcherny should be in synthesis"
