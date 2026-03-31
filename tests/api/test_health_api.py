from __future__ import annotations

from fastapi.testclient import TestClient

from universal_agent.api import server as api_server


def test_api_health_includes_observability(monkeypatch):
    monkeypatch.setattr(
        api_server,
        "get_logfire_runtime_state",
        lambda: {
            "mode": "stub",
            "token_present": True,
            "error": "StopIteration",
            "reason": "otel context entry point missing",
        },
    )

    client = TestClient(api_server.app)
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["observability"] == {
        "mode": "stub",
        "token_present": True,
        "error": "StopIteration",
        "reason": "otel context entry point missing",
    }
