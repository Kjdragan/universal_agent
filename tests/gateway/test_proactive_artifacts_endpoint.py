from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from universal_agent import gateway_server, proactive_signals
from universal_agent.services.proactive_artifacts import upsert_artifact


def _disable_lifespan(monkeypatch):
    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)


def test_proactive_artifacts_list_feedback_and_digest_preview(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve()))
    monkeypatch.setenv("UA_PRIMARY_EMAIL", "kevinjdragan@gmail.com")
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    _disable_lifespan(monkeypatch)

    conn = gateway_server._activity_connect()
    try:
        proactive_signals.upsert_generated_card(
            conn,
            {
                "card_id": "youtube-video:artifact-endpoint",
                "source": "youtube",
                "card_type": "signal_card",
                "title": "Endpoint artifact candidate",
                "summary": "A proactive candidate ready for artifact inventory.",
                "priority": 3,
                "evidence": [{"url": "https://example.test/video"}],
            },
        )
    finally:
        conn.close()

    with TestClient(gateway_server.app) as client:
        listed = client.get("/api/v1/dashboard/proactive-artifacts")
        assert listed.status_code == 200
        artifacts = listed.json()["artifacts"]
        assert any(item["title"] == "Endpoint artifact candidate" for item in artifacts)

        artifact_id = next(item["artifact_id"] for item in artifacts if item["title"] == "Endpoint artifact candidate")
        feedback = client.post(
            f"/api/v1/dashboard/proactive-artifacts/{artifact_id}/feedback",
            json={"score": 5, "feedback_text": "more like this"},
        )
        assert feedback.status_code == 200
        assert feedback.json()["artifact"]["feedback"]["last_score"] == 5

        digest = client.get("/api/v1/dashboard/proactive-artifacts/digest/preview?limit=5")
        assert digest.status_code == 200
        body = digest.json()
        assert body["to"] == "kevinjdragan@gmail.com"
        assert "Endpoint artifact candidate" in body["text"]
        assert "[UA Digest]" in body["subject"]

        monkeypatch.setattr(
            "universal_agent.services.gws_calendar_context.today_calendar_context",
            lambda: {"ok": True, "reason": "", "events": [{"start": "09:00", "summary": "Planning review"}]},
        )
        digest_with_calendar = client.get(
            "/api/v1/dashboard/proactive-artifacts/digest/preview?limit=5&include_calendar=true"
        )
        assert digest_with_calendar.status_code == 200
        assert "Planning review" in digest_with_calendar.json()["text"]
        assert digest_with_calendar.json()["calendar"]["ok"] is True

        weekly = client.get("/api/v1/dashboard/proactive-artifacts/preferences/weekly/preview")
        assert weekly.status_code == 200
        assert "[UA Weekly]" in weekly.json()["subject"]
        assert "Weekly preference model update" in weekly.json()["text"]


def test_proactive_artifact_send_review_reports_missing_agentmail(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve()))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    monkeypatch.setattr(gateway_server, "_agentmail_service", None)
    _disable_lifespan(monkeypatch)

    conn = gateway_server._activity_connect()
    try:
        artifact = upsert_artifact(
            conn,
            artifact_type="signal_brief",
            source_kind="unit",
            source_ref="unit",
            title="Missing mail service candidate",
        )
    finally:
        conn.close()

    with TestClient(gateway_server.app) as client:
        result = client.post(
            f"/api/v1/dashboard/proactive-artifacts/{artifact['artifact_id']}/send-review",
            json={},
        )
        assert result.status_code == 503
        assert "AgentMail service not initialized" in result.text


def test_proactive_codie_cleanup_and_pr_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve()))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    _disable_lifespan(monkeypatch)

    with TestClient(gateway_server.app) as client:
        queued = client.post(
            "/api/v1/dashboard/proactive-artifacts/codie/cleanup-task",
            json={"theme": "reduce brittle routing heuristics", "priority": 3},
        )
        assert queued.status_code == 200
        assert queued.json()["task"]["source_kind"] == "proactive_codie"
        assert queued.json()["artifact"]["artifact_type"] == "codie_cleanup_task"

        registered = client.post(
            "/api/v1/dashboard/proactive-artifacts/codie/pr",
            json={
                "pr_url": "https://github.com/Kjdragan/universal_agent/pull/123",
                "title": "Clean up routing prompt drift",
                "summary": "CODIE opened a draft PR.",
                "branch": "codie/cleanup-routing",
                "theme": "routing cleanup",
                "tests": "uv run pytest tests/test_llm_classifier.py -q",
                "risk": "narrow",
            },
        )
        assert registered.status_code == 200
        assert registered.json()["artifact"]["artifact_type"] == "codie_pr"


def test_proactive_tutorial_build_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve()))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    _disable_lifespan(monkeypatch)

    with TestClient(gateway_server.app) as client:
        queued = client.post(
            "/api/v1/dashboard/proactive-artifacts/tutorial/build-task",
            json={
                "video_id": "abc123",
                "video_title": "Build an MCP server",
                "video_url": "https://youtube.test/watch?v=abc123",
                "channel_name": "AI Builder",
                "extraction_plan": {"language": "python"},
            },
        )
        assert queued.status_code == 200
        assert queued.json()["task"]["source_kind"] == "tutorial_build"
        assert queued.json()["artifact"]["artifact_type"] == "tutorial_build_task"

        registered = client.post(
            "/api/v1/dashboard/proactive-artifacts/tutorial/build-artifact",
            json={
                "video_id": "abc123",
                "title": "Private MCP server demo",
                "repo_url": "https://github.com/Kjdragan/private-mcp-demo",
                "run_commands": "uv run python server.py",
                "tests": "uv run pytest -q",
            },
        )
        assert registered.status_code == 200
        artifact = registered.json()["artifact"]
        assert artifact["artifact_type"] == "tutorial_build"
        assert artifact["metadata"]["repo_visibility"] == "private"


def test_proactive_convergence_signature_endpoint_queues_brief(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve()))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    _disable_lifespan(monkeypatch)

    with TestClient(gateway_server.app) as client:
        first = client.post(
            "/api/v1/dashboard/proactive-artifacts/convergence/signature",
            json={
                "video_id": "video-a",
                "channel_id": "channel-a",
                "channel_name": "Channel A",
                "ingested_at": "2026-04-15T10:00:00+00:00",
                "primary_topics": ["MCP servers"],
                "detect": False,
            },
        )
        assert first.status_code == 200

        second = client.post(
            "/api/v1/dashboard/proactive-artifacts/convergence/signature",
            json={
                "video_id": "video-b",
                "channel_id": "channel-b",
                "channel_name": "Channel B",
                "video_title": "Why MCP matters",
                "video_url": "https://youtube.test/b",
                "ingested_at": "2026-04-15T11:00:00+00:00",
                "primary_topics": ["MCP servers"],
                "key_claims": ["MCP is central for agent tool integration."],
            },
        )
        assert second.status_code == 200
        body = second.json()
        assert body["convergence"]["event"]["primary_topic"] == "MCP servers"
        assert body["convergence"]["artifact"]["artifact_type"] == "convergence_brief_task"


def test_proactive_convergence_extract_endpoint_uses_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve()))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    _disable_lifespan(monkeypatch)

    signature_response = (
        '{"primary_topics":["MCP servers"],"secondary_topics":["agent tools"],'
        '"key_claims":["MCP connects agents to tools."],"content_type":"analysis"}'
    )
    match_response = '{"matches":[{"video_id":"video-a","reason":"same MCP agent tooling topic"}]}'

    with patch("universal_agent.services.llm_classifier._call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [signature_response, signature_response, match_response]
        with TestClient(gateway_server.app) as client:
            first = client.post(
                "/api/v1/dashboard/proactive-artifacts/convergence/extract",
                json={
                    "video_id": "video-a",
                    "title": "MCP agent tools",
                    "summary_text": "MCP connects agents to tools.",
                    "channel_id": "channel-a",
                    "channel_name": "Channel A",
                    "ingested_at": "2026-04-15T10:00:00+00:00",
                    "detect": False,
                },
            )
            assert first.status_code == 200
            second = client.post(
                "/api/v1/dashboard/proactive-artifacts/convergence/extract",
                json={
                    "video_id": "video-b",
                    "title": "Agent tool protocols",
                    "summary_text": "Tool protocols are changing agent workflows.",
                    "channel_id": "channel-b",
                    "channel_name": "Channel B",
                    "ingested_at": "2026-04-15T11:00:00+00:00",
                    "detect": True,
                    "use_llm_match": True,
                },
            )

    assert second.status_code == 200
    body = second.json()
    assert body["signature"]["metadata"]["signature_method"] == "llm"
    assert body["convergence"]["artifact"]["artifact_type"] == "convergence_brief_task"
