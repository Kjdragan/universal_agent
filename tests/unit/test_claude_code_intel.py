from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx

from universal_agent import task_hub
from universal_agent.services.claude_code_intel import (
    ClaudeCodeIntelConfig,
    classify_post,
    extract_links,
    run_sync,
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
        max_results=10,
        queue_task_hub=queue_task_hub,
        artifacts_root=tmp_path,
    )


def test_extract_links_prefers_expanded_urls() -> None:
    post = {
        "text": "Read https://t.co/short",
        "entities": {"urls": [{"url": "https://t.co/short", "expanded_url": "https://docs.x.com/x-api/overview"}]},
    }

    assert extract_links(post) == ["https://docs.x.com/x-api/overview", "https://t.co/short"]


def test_classify_post_escalates_code_release_with_links() -> None:
    action = classify_post(
        {
            "id": "999",
            "text": "Claude Code SDK release with new API workflow and migration guide",
            "entities": {"urls": [{"expanded_url": "https://docs.anthropic.com/claude-code"}]},
        },
        handle="ClaudeDevs",
    )

    assert action["tier"] == 4
    assert action["action_type"] == "strategic_follow_up"
    assert action["url"] == "https://x.com/ClaudeDevs/status/999"


def test_run_sync_writes_packet_and_state_without_task_for_tier_two(tmp_path: Path) -> None:
    client = _client_for_posts(
        [
            {
                "id": "101",
                "text": "Claude Code docs update: read the new guide",
                "created_at": "2026-04-19T12:00:00.000Z",
                "entities": {"urls": [{"expanded_url": "https://docs.anthropic.com/claude-code"}]},
            }
        ]
    )
    conn = sqlite3.connect(":memory:")

    result = run_sync(config=_config(tmp_path), bearer_token="token", conn=conn, client=client)

    assert result.ok is True
    assert result.new_post_count == 1
    assert result.queued_task_count == 0
    packet = Path(result.packet_dir)
    assert (packet / "manifest.json").exists()
    assert (packet / "raw_posts.json").exists()
    assert json.loads((packet / "new_posts.json").read_text(encoding="utf-8"))[0]["id"] == "101"
    state = json.loads((tmp_path / "proactive" / "claude_code_intel" / "state.json").read_text(encoding="utf-8"))
    assert state["last_seen_post_id"] == "101"


def test_run_sync_dedupes_seen_post_ids(tmp_path: Path) -> None:
    posts = [{"id": "201", "text": "Claude Code docs update", "created_at": "2026-04-19T12:00:00.000Z"}]
    conn = sqlite3.connect(":memory:")

    first = run_sync(config=_config(tmp_path, queue_task_hub=False), bearer_token="token", conn=conn, client=_client_for_posts(posts))
    second = run_sync(config=_config(tmp_path, queue_task_hub=False), bearer_token="token", conn=conn, client=_client_for_posts(posts))

    assert first.new_post_count == 1
    assert second.ok is True
    assert second.new_post_count == 0


def test_run_sync_queues_task_hub_for_tier_three(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    posts = [
        {
            "id": "301",
            "text": "New Claude Code SDK API feature with demo repo and workflow example",
            "created_at": "2026-04-19T12:00:00.000Z",
            "entities": {"urls": [{"expanded_url": "https://github.com/example/demo"}]},
        }
    ]

    result = run_sync(config=_config(tmp_path), bearer_token="token", conn=conn, client=_client_for_posts(posts))

    assert result.ok is True
    assert result.queued_task_count == 1
    task_hub.ensure_schema(conn)
    rows = conn.execute("SELECT * FROM task_hub_items WHERE source_ref = '301'").fetchall()
    assert len(rows) == 1
    item = task_hub.hydrate_item(dict(rows[0]))
    assert item["source_kind"] == "claude_code_demo_task"
    assert "claude-code-intel" in item["labels"]


def test_run_sync_writes_failure_packet_when_token_missing(tmp_path: Path) -> None:
    result = run_sync(config=_config(tmp_path), bearer_token="", conn=None, client=_client_for_posts([]))

    assert result.ok is False
    assert "missing X_BEARER_TOKEN" in result.error
    packet = Path(result.packet_dir)
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ok"] is False
    assert (tmp_path / "knowledge-bases" / "claude-code-intelligence" / "source_index.md").exists()
