from __future__ import annotations

import sys
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
DEV_ROOT = PROJECT_ROOT / "CSI_Ingester" / "development"
if str(DEV_ROOT) not in sys.path:
    sys.path.insert(0, str(DEV_ROOT))

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.signature import generate_signature
import universal_agent.gateway_server as gateway_server


class _HookStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch_internal_payload(self, *, subpath, payload, headers=None):
        self.calls.append({"subpath": subpath, "payload": payload, "headers": headers or {}})
        return True, "agent"


def _payload(instance_id: str) -> dict:
    event = CreatorSignalEvent(
        event_id=f"evt-{uuid.uuid4().hex[:8]}",
        dedupe_key="youtube:video:dQw4w9WgXcQ:PL_TEST",
        source="youtube_playlist",
        event_type="video_added_to_playlist",
        occurred_at="2026-02-22T00:00:00Z",
        received_at="2026-02-22T00:00:01Z",
        subject={
            "platform": "youtube",
            "video_id": "dQw4w9WgXcQ",
            "playlist_id": "PL_TEST",
            "channel_id": "UC_TEST",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "Smoke Test Video",
            "published_at": "2026-02-22T00:00:00Z",
        },
        routing={"pipeline": "youtube_tutorial_explainer", "priority": "urgent"},
        metadata={"source_adapter": "smoke_test"},
    )
    return {
        "csi_version": "1.0.0",
        "csi_instance_id": instance_id,
        "batch_id": f"batch_{uuid.uuid4().hex[:10]}",
        "events": [event.model_dump()],
    }


def test_csi_to_ua_local_smoke(monkeypatch):
    shared_secret = "csi-local-smoke-secret"
    instance_id = "csi-local-smoke"
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", shared_secret)
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", instance_id)
    monkeypatch.setenv("CSI_UA_SHARED_SECRET", shared_secret)
    monkeypatch.setenv("CSI_INSTANCE_ID", instance_id)

    hook_stub = _HookStub()

    payload = _payload(instance_id)
    request_id = f"req_{uuid.uuid4().hex[:10]}"
    signature_hex, timestamp = generate_signature(shared_secret, request_id, payload)
    headers = {
        "Authorization": f"Bearer {shared_secret}",
        "X-CSI-Signature": f"sha256={signature_hex}",
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Request-ID": request_id,
    }

    with TestClient(gateway_server.app) as client:
        client.get("/api/v1/health")
        monkeypatch.setattr(gateway_server, "_hooks_service", hook_stub)
        response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert body["internal_dispatches"] == 1
    assert len(hook_stub.calls) == 1
    assert hook_stub.calls[0]["subpath"] == "youtube/manual"
    assert "youtube.com/watch?v=dQw4w9WgXcQ" in hook_stub.calls[0]["payload"]["video_url"]
