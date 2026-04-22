from __future__ import annotations

import json
from pathlib import Path

from universal_agent.services.claude_code_intel_operator_report import (
    artifact_file_url,
    build_operator_email,
    build_operator_report,
)
from universal_agent.scripts.claude_code_intel_run_report import _should_send_email


def test_artifact_file_url_builds_api_path(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    packet_dir = artifacts_root / "proactive" / "claude_code_intel" / "packets" / "2026-04-22" / "101500__ClaudeDevs"
    packet_dir.mkdir(parents=True)
    digest = packet_dir / "digest.md"
    digest.write_text("# Digest\n", encoding="utf-8")

    url = artifact_file_url(digest, artifacts_root=artifacts_root, frontend_url="https://app.clearspringcg.com")

    assert url == (
        "https://app.clearspringcg.com/api/artifacts/files/"
        "proactive/claude_code_intel/packets/2026-04-22/101500__ClaudeDevs/digest.md"
    )


def test_build_operator_report_writes_summary_and_urls(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    packet_dir = artifacts_root / "proactive" / "claude_code_intel" / "packets" / "2026-04-22" / "101500__ClaudeDevs"
    vault_root = artifacts_root / "knowledge-vaults" / "claude-code-intelligence"
    (vault_root / "sources").mkdir(parents=True, exist_ok=True)
    packet_dir.mkdir(parents=True, exist_ok=True)

    (packet_dir / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-04-22T10:15:00+00:00", "handle": "ClaudeDevs", "ok": True}),
        encoding="utf-8",
    )
    (packet_dir / "actions.json").write_text(
        json.dumps(
            [
                {
                    "post_id": "123",
                    "action_type": "strategic_follow_up",
                    "tier": 4,
                    "text": "New Claude Code release with migration guidance.",
                    "url": "https://x.com/ClaudeDevs/status/123",
                }
            ]
        ),
        encoding="utf-8",
    )
    (packet_dir / "candidate_ledger.json").write_text(
        json.dumps(
            [
                {
                    "post_id": "123",
                    "tier": 4,
                    "action_type": "strategic_follow_up",
                    "task_id": "kb:123",
                    "assignment_ids": ["asg_1"],
                    "email_evidence_ids": ["msg_1"],
                    "wiki_pages": ["sources/claudedevs-post-123.md"],
                    "post_url": "https://x.com/ClaudeDevs/status/123",
                }
            ]
        ),
        encoding="utf-8",
    )
    (packet_dir / "linked_sources.json").write_text(
        json.dumps(
            [
                {"url": "https://docs.example.com/release", "fetch_status": "fetched"},
                {"url": "https://github.com/example/repo", "fetch_status": "skipped"},
            ]
        ),
        encoding="utf-8",
    )
    (packet_dir / "digest.md").write_text("# Digest\n", encoding="utf-8")
    (packet_dir / "implementation_opportunities.md").write_text("# Opportunities\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Vault\n", encoding="utf-8")
    (vault_root / "sources" / "claudedevs-post-123.md").write_text("# Source\n", encoding="utf-8")

    summary = build_operator_report(
        sync_payload={
            "ok": True,
            "generated_at": "2026-04-22T10:15:00+00:00",
            "handle": "ClaudeDevs",
            "packet_dir": str(packet_dir),
            "new_post_count": 1,
            "seen_post_count": 1,
            "action_count": 1,
            "queued_task_count": 1,
            "artifact_id": "pa_packet",
            "post_process": {
                "packet_artifact_id": "pa_packet",
                "vault_path": str(vault_root),
                "wiki_pages": ["sources/claudedevs-post-123.md"],
                "lane_ledger_path": str(artifacts_root / "proactive" / "claude_code_intel" / "ledger" / "run.json"),
            },
        },
        artifacts_root=artifacts_root,
        frontend_url="https://app.clearspringcg.com",
    )

    md_path = Path(summary["report_markdown_path"])
    json_path = Path(summary["report_json_path"])
    assert md_path.exists()
    assert json_path.exists()
    assert summary["linked_source_count"] == 2
    assert summary["linked_source_fetched_count"] == 1
    assert summary["top_rows"][0]["task_id"] == "kb:123"
    assert summary["report_markdown_url"].startswith("https://app.clearspringcg.com/api/artifacts/files/")
    assert "The lane keeps a durable last_seen_post_id checkpoint" in md_path.read_text(encoding="utf-8")

    subject, text, html = build_operator_email(summary)
    assert "1 new / 1 actions" in subject
    assert "Candidate ledger" in text
    assert "<a href=" in html


def test_should_send_email_policies() -> None:
    payload = {
        "new_post_count": 1,
        "action_count": 2,
        "queued_task_count": 0,
    }
    assert _should_send_email(policy="always", payload=payload) is True
    assert _should_send_email(policy="when_new_posts", payload=payload) is True
    assert _should_send_email(policy="when_actions", payload=payload) is True
    assert _should_send_email(policy="when_tasks", payload=payload) is False
    assert _should_send_email(policy="never", payload=payload) is False
