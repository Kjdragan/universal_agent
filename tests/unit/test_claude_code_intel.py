from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import httpx

from universal_agent import task_hub
from universal_agent.services.claude_code_intel import (
    ClaudeCodeIntelConfig,
    _oauth1_headers,
    classify_post,
    extract_links,
    fetch_user_by_username_with_fallbacks,
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

    # t.co shortlinks are now filtered out because the X API already provides
    # the expanded URL via entities — keeping both causes duplicate 403 fetches.
    assert extract_links(post) == ["https://docs.x.com/x-api/overview"]


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


def test_classify_post_downshifts_generic_hackathon_announcement(monkeypatch) -> None:
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_LLM_CLASSIFIER_ENABLED", "0")
    action = classify_post(
        {
            "id": "998",
            "text": "Our virtual hackathon is back! Applications are open through Sunday, with build week starting Tuesday.",
            "entities": {"urls": [{"expanded_url": "https://cerebralvalley.ai/e/built-with-4-7-hackathon"}]},
        },
        handle="ClaudeDevs",
    )

    assert action["tier"] == 2
    assert action["action_type"] == "kb_update"
    assert action["classifier"]["content_kind"] == "community_event"


def test_classify_post_uses_llm_override_when_available(monkeypatch) -> None:
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_LLM_CLASSIFIER_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(
        "universal_agent.services.claude_code_intel._call_sync_llm",
        lambda **kwargs: json.dumps(
            {
                "tier": 1,
                "action_type": "digest",
                "content_kind": "community_event",
                "confidence": "high",
                "reasoning": "Community update with low direct implementation value.",
            }
        ),
    )

    action = classify_post(
        {
            "id": "997",
            "text": "Applications are open through Sunday, with build week starting Tuesday!",
            "entities": {"urls": [{"expanded_url": "https://cerebralvalley.ai/e/built-with-4-7-hackathon"}]},
        },
        handle="ClaudeDevs",
    )

    assert action["tier"] == 1
    assert action["action_type"] == "digest"
    assert action["classifier"]["method"] == "llm"
    assert action["classifier"]["confidence"] == "high"


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
    state = json.loads((tmp_path / "proactive" / "claude_code_intel" / "state__claudedevs.json").read_text(encoding="utf-8"))
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


def test_run_sync_writes_failure_packet_when_token_missing(monkeypatch, tmp_path: Path) -> None:
    for key in (
        "X_BEARER_TOKEN",
        "BEARER_TOKEN",
        "X_OAUTH2_ACCESS_TOKEN",
        "X_OAUTH_CONSUMER_KEY",
        "X_OAUTH_CONSUMER_SECRET",
        "X_OAUTH_ACCESS_TOKEN",
        "X_OAUTH_ACCESS_TOKEN_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    result = run_sync(config=_config(tmp_path), bearer_token="", conn=None, client=_client_for_posts([]))

    assert result.ok is False
    assert "missing X_BEARER_TOKEN" in result.error
    packet = Path(result.packet_dir)
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ok"] is False
    assert (tmp_path / "knowledge-bases" / "claude-code-intelligence" / "source_index.md").exists()


def test_oauth2_user_token_fallback_after_app_bearer_forbidden(monkeypatch) -> None:
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "user-token")

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if auth == "Bearer app-token":
            return httpx.Response(403, json={"title": "Client Forbidden"})
        if auth == "Bearer user-token":
            return httpx.Response(200, json={"data": {"id": "12345", "username": "ClaudeDevs"}})
        return httpx.Response(401, json={"title": "Unauthorized"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    payload = fetch_user_by_username_with_fallbacks(client, token="app-token", username="ClaudeDevs")

    assert payload["data"]["id"] == "12345"
    assert payload["_ua_auth_mode"] == "oauth2_user"


def test_oauth1_header_is_constructed_when_credentials_exist(monkeypatch) -> None:
    monkeypatch.setenv("X_OAUTH_CONSUMER_KEY", "consumer-key")
    monkeypatch.setenv("X_OAUTH_CONSUMER_SECRET", "consumer-secret")
    monkeypatch.setenv("X_OAUTH_ACCESS_TOKEN", "access-token")
    monkeypatch.setenv("X_OAUTH_ACCESS_TOKEN_SECRET", "access-secret")

    headers = _oauth1_headers(
        "GET",
        "https://api.x.com/2/users/by/username/ClaudeDevs",
        params={"user.fields": "created_at"},
    )

    assert headers is not None
    assert headers["Authorization"].startswith("OAuth ")
    assert "oauth_consumer_key=\"consumer-key\"" in headers["Authorization"]
    assert "oauth_token=\"access-token\"" in headers["Authorization"]
    assert "consumer-secret" not in headers["Authorization"]
    assert "access-secret" not in headers["Authorization"]


def _client_for_handle(handle: str, *, user_id: str, posts: list[dict]) -> httpx.Client:
    """Create a mock client that responds for any handle."""
    def handler(request: httpx.Request) -> httpx.Response:
        if f"/2/users/by/username/{handle}" in str(request.url):
            return httpx.Response(200, json={"data": {"id": user_id, "username": handle, "name": handle}})
        if f"/2/users/{user_id}/tweets" in str(request.url):
            return httpx.Response(200, json={"data": posts, "meta": {"result_count": len(posts)}})
        return httpx.Response(404, json={"title": "not found"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_run_sync_uses_per_handle_state(tmp_path: Path) -> None:
    """Each handle should get its own state file (state__{handle}.json)."""
    conn = sqlite3.connect(":memory:")
    config_devs = ClaudeCodeIntelConfig(handle="ClaudeDevs", max_results=10, queue_task_hub=False, artifacts_root=tmp_path)
    client_devs = _client_for_handle("ClaudeDevs", user_id="12345", posts=[
        {"id": "501", "text": "ClaudeDevs post", "created_at": "2026-04-23T12:00:00.000Z"},
    ])

    result = run_sync(config=config_devs, bearer_token="token", conn=conn, client=client_devs)
    assert result.ok is True

    lane_root = tmp_path / "proactive" / "claude_code_intel"
    per_handle_state = lane_root / "state__claudedevs.json"
    assert per_handle_state.exists(), f"Expected per-handle state file at {per_handle_state}"

    state = json.loads(per_handle_state.read_text(encoding="utf-8"))
    assert state["handle"] == "ClaudeDevs"
    assert state["last_seen_post_id"] == "501"


def test_run_sync_migrates_legacy_state(tmp_path: Path) -> None:
    """If a legacy state.json exists for a handle, run_sync should read from it and write to per-handle file."""
    lane_root = tmp_path / "proactive" / "claude_code_intel"
    lane_root.mkdir(parents=True, exist_ok=True)

    # Write legacy state with seen_post_ids
    legacy_state = {"handle": "ClaudeDevs", "last_seen_post_id": "400", "seen_post_ids": ["400"]}
    (lane_root / "state.json").write_text(json.dumps(legacy_state), encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    config = ClaudeCodeIntelConfig(handle="ClaudeDevs", max_results=10, queue_task_hub=False, artifacts_root=tmp_path)
    client = _client_for_handle("ClaudeDevs", user_id="12345", posts=[
        {"id": "401", "text": "New post", "created_at": "2026-04-23T12:00:00.000Z"},
    ])

    result = run_sync(config=config, bearer_token="token", conn=conn, client=client)
    assert result.ok is True
    assert result.new_post_count == 1  # should see 401 as new, 400 as seen

    per_handle = lane_root / "state__claudedevs.json"
    assert per_handle.exists(), "Should have written per-handle state file"
    state = json.loads(per_handle.read_text(encoding="utf-8"))
    assert "401" in state["seen_post_ids"]
    assert "400" in state["seen_post_ids"]


def test_two_handles_maintain_separate_state(tmp_path: Path) -> None:
    """Two handles should not share state — each tracks its own seen_post_ids."""
    conn = sqlite3.connect(":memory:")

    # Run ClaudeDevs
    config_devs = ClaudeCodeIntelConfig(handle="ClaudeDevs", max_results=10, queue_task_hub=False, artifacts_root=tmp_path)
    client_devs = _client_for_handle("ClaudeDevs", user_id="12345", posts=[
        {"id": "601", "text": "ClaudeDevs post", "created_at": "2026-04-23T12:00:00.000Z"},
    ])
    result_devs = run_sync(config=config_devs, bearer_token="token", conn=conn, client=client_devs)
    assert result_devs.ok is True
    assert result_devs.new_post_count == 1

    # Run bcherny
    config_boris = ClaudeCodeIntelConfig(handle="bcherny", max_results=10, queue_task_hub=False, artifacts_root=tmp_path)
    client_boris = _client_for_handle("bcherny", user_id="67890", posts=[
        {"id": "602", "text": "Boris post about Claude Code", "created_at": "2026-04-23T12:00:00.000Z"},
    ])
    result_boris = run_sync(config=config_boris, bearer_token="token", conn=conn, client=client_boris)
    assert result_boris.ok is True
    assert result_boris.new_post_count == 1

    lane_root = tmp_path / "proactive" / "claude_code_intel"
    devs_state = json.loads((lane_root / "state__claudedevs.json").read_text(encoding="utf-8"))
    boris_state = json.loads((lane_root / "state__bcherny.json").read_text(encoding="utf-8"))

    assert devs_state["handle"] == "ClaudeDevs"
    assert boris_state["handle"] == "bcherny"
    assert "601" in devs_state["seen_post_ids"]
    assert "601" not in boris_state["seen_post_ids"]
    assert "602" in boris_state["seen_post_ids"]
    assert "602" not in devs_state["seen_post_ids"]
