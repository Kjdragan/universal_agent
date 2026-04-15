from __future__ import annotations

from contextlib import asynccontextmanager

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
