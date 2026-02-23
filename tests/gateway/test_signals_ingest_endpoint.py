import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
import pytest

from universal_agent.gateway_server import app


def _sign(secret: str, request_id: str, timestamp: str, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    signing_string = f"{timestamp}.{request_id}.{body}"
    digest = hmac.new(secret.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _payload(source: str = "youtube_playlist") -> dict:
    return {
        "csi_version": "1.0.0",
        "csi_instance_id": "csi-vps-01",
        "batch_id": "batch_001",
        "events": [
            {
                "event_id": "evt-1",
                "dedupe_key": "youtube:video:dQw4w9WgXcQ:PLX",
                "source": source,
                "event_type": "video_added_to_playlist",
                "occurred_at": "2026-02-22T00:00:00Z",
                "received_at": "2026-02-22T00:00:01Z",
                "subject": {
                    "platform": "youtube",
                    "video_id": "dQw4w9WgXcQ",
                    "channel_id": "UC_TEST",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "title": "Test",
                    "published_at": "2026-02-22T00:00:00Z",
                },
                "routing": {"pipeline": "youtube_tutorial_explainer", "priority": "urgent"},
                "metadata": {"source_adapter": "test"},
                "contract_version": "1.0",
            }
        ],
    }


class _HookStub:
    def __init__(self):
        self.calls = []

    async def dispatch_internal_payload(self, *, subpath, payload, headers=None):
        self.calls.append({"subpath": subpath, "payload": payload, "headers": headers or {}})
        return True, "agent"


@pytest.fixture
def client():
    return TestClient(app)


def test_signals_ingest_dispatches_internal_youtube(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="youtube_playlist")
    request_id = "req-1"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert body["internal_dispatches"] == 1
    assert len(hook_stub.calls) == 1
    assert hook_stub.calls[0]["subpath"] == "youtube/manual"
    assert hook_stub.calls[0]["payload"]["video_id"] == "dQw4w9WgXcQ"


def test_signals_ingest_non_youtube_source_skips_internal_dispatch(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="reddit")
    request_id = "req-2"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert "internal_dispatches" not in body
    assert hook_stub.calls == []


def test_signals_ingest_rss_source_skips_internal_dispatch(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="youtube_channel_rss")
    request_id = "req-3"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert "internal_dispatches" not in body
    assert hook_stub.calls == []
