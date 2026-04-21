from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx

from universal_agent import task_hub
from universal_agent.services.claude_code_intel import ClaudeCodeIntelConfig, run_sync
from universal_agent.services.claude_code_intel_replay import (
    ClaudeCodeIntelReplayConfig,
    replay_packet,
)


def _client_for_posts(posts: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/2/users/by/username/ClaudeDevs":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "12345",
                        "username": "ClaudeDevs",
                        "name": "Claude Code Devs",
                    }
                },
            )
        if request.url.path == "/2/users/12345/tweets":
            return httpx.Response(200, json={"data": posts, "meta": {"result_count": len(posts)}})
        return httpx.Response(404, json={"title": "not found"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _config(tmp_path: Path, *, queue_task_hub: bool = True) -> ClaudeCodeIntelConfig:
    return ClaudeCodeIntelConfig(
        handle="ClaudeDevs",
        max_results=25,
        queue_task_hub=queue_task_hub,
        artifacts_root=tmp_path,
    )


def test_replay_writes_candidate_ledger_and_external_vault(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def fake_ingest(*, vault_slug: str, source_title: str, source_content: str, source_id: str | None = None, root_override: str | None = None):
        calls.append((source_title, source_id or ""))
        vault_root = Path(root_override or tmp_path / "knowledge-vaults" / vault_slug)
        path = vault_root / "sources" / f"{source_id or 'source'}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source_content, encoding="utf-8")
        return {"status": "success", "path": str(path.relative_to(vault_root))}

    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_replay.wiki_ingest_external_source",
        fake_ingest,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    posts = [
        {
            "id": "501",
            "text": "Claude Code SDK feature with demo repo and migration guide",
            "created_at": "2026-04-19T12:00:00.000Z",
            "entities": {"urls": [{"expanded_url": "https://github.com/example/demo"}]},
        },
        {
            "id": "502",
            "text": "Claude Code docs update only",
            "created_at": "2026-04-19T13:00:00.000Z",
        },
    ]
    sync = run_sync(config=_config(tmp_path), bearer_token="token", conn=conn, client=_client_for_posts(posts))

    work_product_dir = tmp_path / "work_products"
    (work_product_dir / "email_verification").mkdir(parents=True, exist_ok=True)
    (work_product_dir / "analysis.md").write_text("# Analysis\n", encoding="utf-8")
    (work_product_dir / "email_verification" / "email_send_1.json").write_text("{}", encoding="utf-8")

    result = replay_packet(
        config=ClaudeCodeIntelReplayConfig(
            packet_dir=Path(sync.packet_dir),
            queue_task_hub=False,
            write_vault=True,
            artifacts_root=tmp_path,
            work_product_dir=work_product_dir,
        ),
        conn=conn,
    )

    assert result["ok"] is True
    ledger = json.loads((Path(sync.packet_dir) / "candidate_ledger.json").read_text(encoding="utf-8"))
    assert len(ledger) == 2
    assert ledger[0]["packet_artifact_id"]
    assert any(entry["task_row_present"] for entry in ledger)
    assert (tmp_path / "proactive" / "claude_code_intel" / "ledger").exists()
    assert (tmp_path / "knowledge-vaults" / "claude-code-intelligence" / "raw" / "packets" / Path(sync.packet_dir).name / "manifest.json").exists()
    assert (Path(sync.packet_dir) / "implementation_opportunities.md").exists()
    assert result["email_evidence_ids"] == ["email_send_1.json"]
    assert len(calls) >= 3  # two posts + one work product


def test_replay_is_idempotent_for_task_hub_rows(monkeypatch, tmp_path: Path) -> None:
    def fake_ingest(**kwargs):
        return {"status": "success", "path": f"sources/{kwargs.get('source_id')}.md"}

    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_replay.wiki_ingest_external_source",
        fake_ingest,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    posts = [
        {
            "id": "601",
            "text": "Claude Code SDK feature with demo repo and workflow example",
            "created_at": "2026-04-19T12:00:00.000Z",
            "entities": {"urls": [{"expanded_url": "https://github.com/example/demo"}]},
        }
    ]
    sync = run_sync(config=_config(tmp_path, queue_task_hub=False), bearer_token="token", conn=conn, client=_client_for_posts(posts))

    cfg = ClaudeCodeIntelReplayConfig(
        packet_dir=Path(sync.packet_dir),
        queue_task_hub=True,
        write_vault=True,
        artifacts_root=tmp_path,
    )
    first = replay_packet(config=cfg, conn=conn)
    second = replay_packet(config=cfg, conn=conn)

    assert first["queued_task_count"] == 1
    rows = conn.execute("SELECT COUNT(*) AS c FROM task_hub_items WHERE source_ref = '601'").fetchone()
    assert rows["c"] == 1
    ledger = json.loads((Path(sync.packet_dir) / "candidate_ledger.json").read_text(encoding="utf-8"))
    assert len(ledger) == 1
    assert second["queued_task_count"] == 1


def test_replay_links_ledger_to_assignments(monkeypatch, tmp_path: Path) -> None:
    def fake_ingest(**kwargs):
        return {"status": "success", "path": f"sources/{kwargs.get('source_id')}.md"}

    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_replay.wiki_ingest_external_source",
        fake_ingest,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key, priority, due_at,
            labels_json, status, must_complete, incident_key, workstream_id, subtask_role,
            parent_task_id, agent_ready, score, score_confidence, stale_state, seizure_state,
            mirror_status, trigger_type, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '', 'proactive', 3, NULL, '[]', 'needs_review', 0, NULL, NULL, NULL, NULL, 1, 0.0, 0.0, 'fresh', 'needs_review', 'internal', 'heartbeat_poll', '{}', '2026-04-20T00:00:00+00:00', '2026-04-20T00:00:00+00:00')
        """,
        (
            "claude_code_demo_task:deadbeefdeadbeef",
            "claude_code_demo_task",
            "701",
            "Build Claude Code demo from @ClaudeDevs update",
        ),
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, workflow_run_id, workflow_attempt_id,
            provider_session_id, state, started_at, ended_at, result_summary, workspace_dir
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "asg_demo_1",
            "claude_code_demo_task:deadbeefdeadbeef",
            "todo:daemon_simone_todo",
            "run_123",
            None,
            "daemon_simone_todo",
            "completed",
            "2026-04-20T00:00:00+00:00",
            "2026-04-20T00:01:00+00:00",
            "sent_email",
            "/tmp/run_123",
        ),
    )
    conn.commit()

    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "manifest.json").write_text(
        json.dumps({"handle": "ClaudeDevs", "ok": True, "new_post_count": 1, "action_count": 1}),
        encoding="utf-8",
    )
    (packet_dir / "raw_posts.json").write_text(json.dumps({}), encoding="utf-8")
    (packet_dir / "new_posts.json").write_text(
        json.dumps([{"id": "701", "text": "demo update", "created_at": "2026-04-19T12:00:00.000Z"}]),
        encoding="utf-8",
    )
    (packet_dir / "actions.json").write_text(
        json.dumps(
            [
                {
                    "post_id": "701",
                    "tier": 3,
                    "action_type": "demo_task",
                    "url": "https://x.com/ClaudeDevs/status/701",
                    "text": "demo update",
                    "links": [],
                    "matched_terms": ["demo"],
                    "reasons": ["implementation opportunity"],
                }
            ]
        ),
        encoding="utf-8",
    )

    result = replay_packet(
        config=ClaudeCodeIntelReplayConfig(
            packet_dir=packet_dir,
            queue_task_hub=False,
            write_vault=True,
            artifacts_root=tmp_path,
        ),
        conn=conn,
    )

    ledger = json.loads((packet_dir / "candidate_ledger.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert ledger[0]["task_row_present"] is False  # deterministic intended task id differs from inserted historical row
    assert ledger[0]["assignment_ids"] == []
