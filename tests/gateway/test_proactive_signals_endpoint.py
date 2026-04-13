from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from universal_agent import gateway_server


def test_proactive_signals_feedback_and_action_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve()))
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    monkeypatch.setattr(gateway_server, "_discord_intelligence_db_path", lambda: tmp_path / "missing_discord.db")
    monkeypatch.setattr(gateway_server, "_csi_default_db_path", lambda: tmp_path / "missing_csi.db")

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)

    conn = gateway_server._activity_connect()
    try:
        from universal_agent.proactive_signals import upsert_generated_card

        upsert_generated_card(
            conn,
            {
                "card_id": "youtube-video:endpoint",
                "source": "youtube",
                "card_type": "diamond",
                "title": "YouTube candidate: endpoint test",
                "summary": "Metadata-only candidate ready for feedback.",
                "actions": [
                    {
                        "id": "fetch_transcripts",
                        "label": "Fetch Transcript",
                        "description": "Fetch and analyze the transcript.",
                    }
                ],
                "evidence": [{"title": "endpoint test", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}],
            },
        )
    finally:
        conn.close()

    with TestClient(gateway_server.app) as client:
        listed = client.get("/api/v1/dashboard/proactive-signals?source=youtube&status=pending")
        assert listed.status_code == 200
        cards = listed.json()["cards"]
        assert [card["card_id"] for card in cards] == ["youtube-video:endpoint"]

        feedback = client.patch(
            "/api/v1/dashboard/proactive-signals/youtube-video:endpoint/feedback",
            json={"feedback_tags": ["novel"], "feedback_text": "Good scout card."},
        )
        assert feedback.status_code == 200
        assert feedback.json()["card"]["feedback"]["tag_counts"]["novel"] == 1

        action = client.post(
            "/api/v1/dashboard/proactive-signals/youtube-video:endpoint/action",
            json={"action_id": "fetch_transcripts"},
        )
        assert action.status_code == 200
        task_id = action.json()["task_id"]
        assert task_id.startswith("proactive_signal:")
