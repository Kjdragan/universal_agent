from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from universal_agent import gateway_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gateway_server, "_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setattr(gateway_server, "SESSION_API_TOKEN", "")
    monkeypatch.setenv("UA_YOUTUBE_INGEST_TOKEN", "ingest-token")

    @asynccontextmanager
    async def _test_lifespan(_app):
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)
    with TestClient(gateway_server.app) as c:
        yield c


def test_youtube_ingest_endpoint_forwards_request_fields_and_returns_metadata(client, monkeypatch):
    captured: dict[str, object] = {}

    def _fake_ingest_youtube_transcript(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "succeeded",
            "video_url": "https://www.youtube.com/watch?v=dxlyCPGCvy8",
            "video_id": "dxlyCPGCvy8",
            "transcript_text": "hello transcript",
            "transcript_chars": 16,
            "source": "youtube_transcript_api",
            "metadata": {"title": "Hello", "channel": "World"},
            "metadata_status": "attempted_failed",
            "metadata_source": "yt_dlp",
            "metadata_error": "yt_dlp_metadata_failed",
            "metadata_failure_class": "request_blocked",
            "attempts": [
                {"method": "youtube_transcript_api", "ok": True},
                {"method": "yt_dlp_metadata", "ok": False, "error": "yt_dlp_metadata_failed"},
            ],
        }

    monkeypatch.setattr(gateway_server, "ingest_youtube_transcript", _fake_ingest_youtube_transcript)

    response = client.post(
        "/api/v1/youtube/ingest",
        headers={"Authorization": "Bearer ingest-token"},
        json={
            "video_url": "https://www.youtube.com/watch?v=dxlyCPGCvy8",
            "video_id": "dxlyCPGCvy8",
            "language": "es",
            "timeout_seconds": 77,
            "max_chars": 123456,
            "min_chars": 321,
            "request_id": "req-123",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert captured == {
        "video_url": "https://www.youtube.com/watch?v=dxlyCPGCvy8",
        "video_id": "dxlyCPGCvy8",
        "language": "es",
        "timeout_seconds": 77,
        "max_chars": 123456,
        "min_chars": 321,
    }
    assert body["request_id"] == "req-123"
    assert body["worker_profile"] == "local_workstation"
    assert body["metadata"] == {"title": "Hello", "channel": "World"}
    assert body["metadata_status"] == "attempted_failed"
    assert body["metadata_source"] == "yt_dlp"
    assert body["metadata_error"] == "yt_dlp_metadata_failed"
    assert body["metadata_failure_class"] == "request_blocked"
    assert body["attempts"][1]["method"] == "yt_dlp_metadata"


def test_youtube_ingest_endpoint_requires_video_target(client):
    response = client.post(
        "/api/v1/youtube/ingest",
        headers={"Authorization": "Bearer ingest-token"},
        json={"request_id": "req-missing"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "video_url or valid video_id is required"
