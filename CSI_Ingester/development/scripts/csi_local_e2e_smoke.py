#!/usr/bin/env python3
"""Local CSI -> UA ingest smoke test with internal hook dispatch verification."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
DEV_ROOT = Path(__file__).resolve().parents[1]
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


def _build_payload(instance_id: str) -> dict:
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
        routing={"pipeline": "youtube_tutorial_explainer", "priority": "urgent", "tags": ["youtube", "playlist"]},
        metadata={"source_adapter": "smoke_test"},
    )
    return {
        "csi_version": "1.0.0",
        "csi_instance_id": instance_id,
        "batch_id": f"batch_{uuid.uuid4().hex[:10]}",
        "events": [event.model_dump()],
    }


def main() -> int:
    shared_secret = (os.getenv("CSI_UA_SHARED_SECRET") or "csi-smoke-secret").strip()
    instance_id = (os.getenv("CSI_INSTANCE_ID") or "csi-local-smoke").strip()
    os.environ["UA_SIGNALS_INGEST_ENABLED"] = "1"
    os.environ["UA_SIGNALS_INGEST_SHARED_SECRET"] = shared_secret
    os.environ["UA_SIGNALS_INGEST_ALLOWED_INSTANCES"] = instance_id

    payload = _build_payload(instance_id)
    request_id = f"req_{uuid.uuid4().hex[:10]}"
    signature_hex, timestamp = generate_signature(shared_secret, request_id, payload)
    headers = {
        "Authorization": f"Bearer {shared_secret}",
        "X-CSI-Signature": f"sha256={signature_hex}",
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Request-ID": request_id,
    }

    original_hooks_service = getattr(gateway_server, "_hooks_service", None)
    hook_stub = _HookStub()
    try:
        with TestClient(gateway_server.app) as client:
            client.get("/api/v1/health")
            gateway_server._hooks_service = hook_stub
            resp = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
        if resp.status_code != 200:
            print(f"SMOKE_FAIL status={resp.status_code} body={resp.text[:400]}")
            return 1
        body = resp.json() if resp.content else {}
        if int(body.get("accepted", 0)) != 1:
            print(f"SMOKE_FAIL accepted={body.get('accepted')} body={body}")
            return 1
        if int(body.get("internal_dispatches", 0)) != 1:
            print(f"SMOKE_FAIL internal_dispatches={body.get('internal_dispatches')} body={body}")
            return 1
        if len(hook_stub.calls) != 1:
            print(f"SMOKE_FAIL dispatch_calls={len(hook_stub.calls)}")
            return 1
        call = hook_stub.calls[0]
        if call.get("subpath") != "youtube/manual":
            print(f"SMOKE_FAIL wrong_subpath={call.get('subpath')}")
            return 1
        video_url = str((call.get("payload") or {}).get("video_url") or "")
        if "youtube.com/watch?v=dQw4w9WgXcQ" not in video_url:
            print(f"SMOKE_FAIL wrong_video_url={video_url}")
            return 1
        print("SMOKE_OK status=200 accepted=1 internal_dispatches=1")
        return 0
    finally:
        gateway_server._hooks_service = original_hooks_service


if __name__ == "__main__":
    raise SystemExit(main())
