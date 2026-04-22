from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx

from universal_agent import task_hub
from universal_agent.services import proactive_artifacts
from universal_agent.services.claude_code_intel import ClaudeCodeIntelConfig, run_sync
from universal_agent.services.claude_code_intel_replay import (
    ClaudeCodeIntelReplayConfig,
    intended_task_identity,
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
    assert ledger[0]["post_source_pages"]
    assert ledger[0]["work_product_pages"]
    assert any(entry["task_row_present"] for entry in ledger)
    assert (tmp_path / "proactive" / "claude_code_intel" / "ledger").exists()
    assert (tmp_path / "knowledge-vaults" / "claude-code-intelligence" / "raw" / "packets" / Path(sync.packet_dir).name / "manifest.json").exists()
    assert (Path(sync.packet_dir) / "implementation_opportunities.md").exists()
    assert result["email_evidence_ids"] == ["email_send_1.json"]
    assert len(calls) >= 3  # two posts + one work product


def test_replay_fetches_linked_sources_and_writes_metadata(monkeypatch, tmp_path: Path) -> None:
    def fake_ingest(*, vault_slug: str, source_title: str, source_content: str, source_id: str | None = None, root_override: str | None = None):
        vault_root = Path(root_override or tmp_path / "knowledge-vaults" / vault_slug)
        path = vault_root / "sources" / f"{source_id or 'source'}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source_content, encoding="utf-8")
        return {"status": "success", "path": str(path.relative_to(vault_root))}

    class FakeResponse:
        def __init__(self, url: str):
            self.status_code = 200
            self.text = "<html><head><title>Demo Repo</title></head><body><h1>Demo Repo</h1><p>Install and build the package.</p></body></html>"
            self.headers = {"content-type": "text/html"}
            self.url = url

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str):
            return FakeResponse(url)

    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_replay.wiki_ingest_external_source",
        fake_ingest,
    )
    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_replay.httpx.Client",
        FakeClient,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    packet_dir = tmp_path / "packet_fetch"
    packet_dir.mkdir()
    (packet_dir / "manifest.json").write_text(
        json.dumps({"handle": "ClaudeDevs", "ok": True, "new_post_count": 1, "action_count": 1}),
        encoding="utf-8",
    )
    (packet_dir / "raw_posts.json").write_text(json.dumps({}), encoding="utf-8")
    (packet_dir / "new_posts.json").write_text(
        json.dumps(
            [
                {
                    "id": "551",
                    "text": "Claude Code SDK feature with demo repo and migration guide",
                    "created_at": "2026-04-19T12:00:00.000Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    (packet_dir / "actions.json").write_text(
        json.dumps(
            [
                {
                    "post_id": "551",
                    "tier": 3,
                    "action_type": "demo_task",
                    "url": "https://x.com/ClaudeDevs/status/551",
                    "text": "Claude Code SDK feature with demo repo and migration guide",
                    "links": ["https://github.com/example/demo"],
                    "matched_terms": ["demo", "repo"],
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
            expand_sources=True,
            artifacts_root=tmp_path,
        ),
        conn=conn,
    )

    linked = json.loads((packet_dir / "linked_sources.json").read_text(encoding="utf-8"))
    assert result["linked_source_count"] == 1
    assert result["linked_source_fetched_count"] == 1
    assert linked[0]["fetch_status"] == "fetched"
    source_path = Path(linked[0]["source_path"])
    analysis_path = Path(linked[0]["analysis_path"])
    metadata_path = Path(linked[0]["metadata_path"])
    assert source_path.exists()
    assert analysis_path.exists()
    assert metadata_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["title"] == "Demo Repo"
    assert metadata["http_status"] == 200
    assert metadata["source_type"] == "github_repo"
    assert metadata["github_repo"] == "example/demo"
    assert "Install and build the package." in source_path.read_text(encoding="utf-8")
    analysis_text = analysis_path.read_text(encoding="utf-8")
    assert linked[0]["analysis_path"].endswith("analysis.md")
    assert "GitHub repository page" in analysis_text


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
    assert ledger[0]["candidate_artifact_id"]


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
    task_id = intended_task_identity(post_id="701", tier=3)["task_id"]
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
            task_id,
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
            task_id,
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
    assert ledger[0]["task_row_present"] is True
    assert ledger[0]["task_id"] == task_id
    assert ledger[0]["assignment_ids"] == ["asg_demo_1"]


def test_replay_hydrates_email_evidence_from_task_and_assignment_workspace(monkeypatch, tmp_path: Path) -> None:
    def fake_ingest(**kwargs):
        return {"status": "success", "path": f"sources/{kwargs.get('source_id')}.md"}

    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel_replay.wiki_ingest_external_source",
        fake_ingest,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    proactive_artifacts.ensure_schema(conn)
    task_id = intended_task_identity(post_id="801", tier=4)["task_id"]
    proactive_artifacts.upsert_artifact(
        conn,
        artifact_id="pa_task_801",
        artifact_type="claude_code_follow_up_task",
        source_kind="claude_code_kb_update",
        source_ref="801",
        title="Analyze strategic Claude Code update from @ClaudeDevs",
        summary="candidate",
    )
    workspace_dir = tmp_path / "run_801"
    verification_dir = workspace_dir / "work_products" / "email_verification"
    verification_dir.mkdir(parents=True, exist_ok=True)
    (verification_dir / "email_send_task_801.json").write_text("{}", encoding="utf-8")
    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key, priority, due_at,
            labels_json, status, must_complete, incident_key, workstream_id, subtask_role,
            parent_task_id, agent_ready, score, score_confidence, stale_state, seizure_state,
            mirror_status, trigger_type, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '', 'proactive', 4, NULL, '[]', 'completed', 0, NULL, NULL, NULL, NULL, 1, 0.0, 0.0, 'fresh', 'completed', 'internal', 'heartbeat_poll', ?, '2026-04-20T00:00:00+00:00', '2026-04-20T00:00:00+00:00')
        """,
        (
            task_id,
            "claude_code_kb_update",
            "801",
            "Analyze strategic Claude Code update from @ClaudeDevs",
            json.dumps({"dispatch": {"outbound_delivery": {"channel": "agentmail", "message_id": "msg-801", "sent_at": "2026-04-20T00:01:00+00:00"}}}),
        ),
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_task_mappings (
            thread_id TEXT PRIMARY KEY,
            task_id TEXT,
            ack_message_id TEXT DEFAULT '',
            ack_draft_id TEXT DEFAULT '',
            final_message_id TEXT DEFAULT '',
            final_draft_id TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        INSERT INTO email_task_mappings (thread_id, task_id, final_message_id)
        VALUES (?, ?, ?)
        """,
        ("thread_801", task_id, "msg-email-map-801"),
    )
    proactive_artifacts.record_email_delivery(
        conn,
        artifact_id="pa_task_801",
        message_id="msg-artifact-801",
        thread_id="thread-artifact-801",
        subject="Claude Code Intel",
        recipient="kevin@example.com",
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, workflow_run_id, workflow_attempt_id,
            provider_session_id, state, started_at, ended_at, result_summary, workspace_dir
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "asg_kb_801",
            task_id,
            "todo:daemon_simone_todo",
            "run_801",
            None,
            "daemon_simone_todo",
            "completed",
            "2026-04-20T00:00:00+00:00",
            "2026-04-20T00:05:00+00:00",
            "sent_email",
            str(workspace_dir),
        ),
    )
    conn.commit()

    packet_dir = tmp_path / "packet_801"
    packet_dir.mkdir()
    (packet_dir / "manifest.json").write_text(
        json.dumps({"handle": "ClaudeDevs", "ok": True, "new_post_count": 1, "action_count": 1}),
        encoding="utf-8",
    )
    (packet_dir / "raw_posts.json").write_text(json.dumps({}), encoding="utf-8")
    (packet_dir / "new_posts.json").write_text(
        json.dumps([{"id": "801", "text": "strategic update", "created_at": "2026-04-19T12:00:00.000Z"}]),
        encoding="utf-8",
    )
    (packet_dir / "actions.json").write_text(
        json.dumps(
            [
                {
                    "post_id": "801",
                    "tier": 4,
                    "action_type": "strategic_follow_up",
                    "url": "https://x.com/ClaudeDevs/status/801",
                    "text": "strategic update",
                    "links": [],
                    "matched_terms": ["update"],
                    "reasons": ["strategic"],
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
    assert ledger[0]["candidate_artifact_id"] == "pa_task_801"
    assert ledger[0]["assignment_ids"] == ["asg_kb_801"]
    assert ledger[0]["assignment_workspaces"] == [str(workspace_dir)]
    assert "msg-801" in ledger[0]["email_evidence_ids"]
    assert "email_send_task_801.json" in ledger[0]["email_evidence_ids"]
    assert "msg-email-map-801" in ledger[0]["email_evidence_ids"]
    assert "msg-artifact-801" in ledger[0]["email_evidence_ids"]
    assert ledger[0]["task_outbound_delivery"]["message_id"] == "msg-801"
