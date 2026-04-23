from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent import gateway_server


@pytest.mark.asyncio
async def test_claude_code_intel_dashboard_endpoint_returns_latest_packet_and_vault(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path)

    packet_dir = tmp_path / "proactive" / "claude_code_intel" / "packets" / "2026-04-23" / "161449__ClaudeDevs"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "proactive" / "claude_code_intel" / "state.json").write_text(
        json.dumps(
            {
                "handle": "ClaudeDevs",
                "last_seen_post_id": "2047086372666921217",
                "last_success_at": "2026-04-23T16:14:49.256307+00:00",
                "seen_post_ids": ["1", "2", "3"],
            }
        ),
        encoding="utf-8",
    )
    (packet_dir / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-04-23T16:14:49.256307+00:00",
                "handle": "ClaudeDevs",
                "new_post_count": 3,
                "action_count": 3,
                "ok": True,
            }
        ),
        encoding="utf-8",
    )
    (packet_dir / "operator_report.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-04-23T16:14:49.256307+00:00",
                "handle": "ClaudeDevs",
                "ok": True,
                "new_post_count": 3,
                "action_count": 3,
                "queued_task_count": 1,
                "linked_source_count": 6,
                "linked_source_fetched_count": 4,
                "tier_counts": {"2": 1, "3": 1, "4": 1},
                "action_type_counts": {"demo_task": 1},
                "top_rows": [{"post_id": "123", "tier": 3, "action_type": "demo_task"}],
            }
        ),
        encoding="utf-8",
    )
    for name in ("operator_report.md", "digest.md", "candidate_ledger.json", "linked_sources.json", "implementation_opportunities.md"):
        (packet_dir / name).write_text("content\n", encoding="utf-8")
    lane_ledger = tmp_path / "proactive" / "claude_code_intel" / "ledger" / "2026-04-23__161449__ClaudeDevs.json"
    lane_ledger.parent.mkdir(parents=True, exist_ok=True)
    lane_ledger.write_text("[]\n", encoding="utf-8")
    (packet_dir / "replay_summary.json").write_text(
        json.dumps({"lane_ledger_path": str(lane_ledger), "linked_source_count": 6, "linked_source_fetched_count": 4}),
        encoding="utf-8",
    )
    rolling_dir = tmp_path / "proactive" / "claude_code_intel" / "rolling" / "current"
    rolling_dir.mkdir(parents=True, exist_ok=True)
    (rolling_dir / "rolling_14_day_report.md").write_text("# Rolling Brief\n", encoding="utf-8")
    (rolling_dir / "rolling_14_day_report.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-04-23T16:20:00+00:00",
                "window_days": 14,
                "title": "Rolling 14-Day Claude Code Builder Brief",
                "bundle_count": 1,
                "narrative_markdown": "# Rolling Brief\n",
                "bundles": [
                    {
                        "bundle_id": "ultrareview",
                        "title": "Ultrareview rollout",
                        "summary": "A new bundle",
                        "recommended_variant": "ua-adaptation",
                        "variants": [],
                    }
                ],
                "repo_outputs": {"bundle_count": 1},
            }
        ),
        encoding="utf-8",
    )
    bundle_dir = rolling_dir / "bundles" / "ultrareview"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "bundle.md").write_text("# Ultrareview\n", encoding="utf-8")
    (bundle_dir / "bundle.json").write_text("{}\n", encoding="utf-8")

    vault_root = tmp_path / "knowledge-vaults" / "claude-code-intelligence"
    (vault_root / "sources").mkdir(parents=True, exist_ok=True)
    (vault_root / "vault_manifest.json").write_text(
        json.dumps({"title": "Claude Code Intelligence", "vault_kind": "external"}),
        encoding="utf-8",
    )
    (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (vault_root / "overview.md").write_text("# Overview\n", encoding="utf-8")
    (vault_root / "log.md").write_text("", encoding="utf-8")
    (vault_root / "sources" / "demo.md").write_text(
        "---\n"
        "title: Demo Source\n"
        "kind: source\n"
        "updated_at: '2026-04-23T16:15:00+00:00'\n"
        "tags:\n"
        "- external\n"
        "- docs\n"
        "summary: Demo summary\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )
    (vault_root / "sources" / "bad-x-shell.md").write_text(
        "---\n"
        "title: Bad X Shell\n"
        "kind: source\n"
        "updated_at: '2026-04-23T16:15:00+00:00'\n"
        "tags:\n"
        "- external\n"
        "- x.com\n"
        "- JavaScript\n"
        "summary: JavaScript is not available. Please enable JavaScript or switch to a supported browser to continue using x.com.\n"
        "---\n\n"
        "# Bad\n",
        encoding="utf-8",
    )

    payload = await gateway_server.dashboard_claude_code_intel(request=object(), limit=20)

    assert payload["status"] == "ok"
    assert payload["state"]["last_seen_post_id"] == "2047086372666921217"
    latest = payload["latest_packet"]
    assert latest["packet_name"] == "161449__ClaudeDevs"
    assert latest["linked_source_fetched_count"] == 4
    assert latest["report_markdown"]["api_url"].endswith("/operator_report.md")
    knowledge_pages = payload["vault"]["knowledge_pages"]
    assert len(knowledge_pages) == 1
    assert knowledge_pages[0]["title"] == "Demo Source"
    assert knowledge_pages[0]["api_url"].endswith("/sources/demo.md")
    assert payload["rolling"]["window_days"] == 14


@pytest.mark.asyncio
async def test_claude_code_intel_trigger_returns_accepted(monkeypatch, tmp_path: Path):
    """POST /api/v1/dashboard/claude-code-intel/trigger should accept a pipeline run request."""
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path)

    # Create the state file so the endpoint can read it
    state_dir = tmp_path / "proactive" / "claude_code_intel"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(
        json.dumps({"handle": "ClaudeDevs", "last_seen_post_id": "123"}),
        encoding="utf-8",
    )

    # Mock the subprocess call so it doesn't actually run
    launched_commands: list[str] = []

    async def _mock_subprocess(*args, **kwargs):
        launched_commands.append(str(args))

        class _FakeProc:
            pid = 12345
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _mock_subprocess)

    payload = await gateway_server.dashboard_claude_code_intel_trigger(
        request=object(),
        action="full_pipeline",
    )
    assert payload["status"] == "accepted"
    assert payload["action"] == "full_pipeline"
    assert "pid" in payload


@pytest.mark.asyncio
async def test_claude_code_intel_trigger_rejects_invalid_action(monkeypatch, tmp_path: Path):
    """POST with an invalid action should return an error."""
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path)

    state_dir = tmp_path / "proactive" / "claude_code_intel"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(
        json.dumps({"handle": "ClaudeDevs"}),
        encoding="utf-8",
    )

    payload = await gateway_server.dashboard_claude_code_intel_trigger(
        request=object(),
        action="invalid_action",
    )
    assert payload["status"] == "error"
    assert "invalid" in payload.get("detail", "").lower() or "unsupported" in payload.get("detail", "").lower()
