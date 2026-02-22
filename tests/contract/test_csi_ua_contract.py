from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.signals_ingest import process_signals_ingest_payload


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSI_DEV_PATH = PROJECT_ROOT / "CSI_Ingester" / "development"
if str(CSI_DEV_PATH) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(CSI_DEV_PATH))

from csi_ingester.contract import CSIIngestBatch, CreatorSignalEvent  # noqa: E402
from csi_ingester.signature import generate_signature  # noqa: E402


def _sample_event() -> CreatorSignalEvent:
    return CreatorSignalEvent(
        event_id="evt-1",
        dedupe_key="youtube:video:dQw4w9WgXcQ:PLX",
        source="youtube_playlist",
        event_type="video_added_to_playlist",
        occurred_at="2026-02-22T00:00:00Z",
        received_at="2026-02-22T00:00:01Z",
        subject={
            "platform": "youtube",
            "video_id": "dQw4w9WgXcQ",
            "channel_id": "UC_TEST",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "Test",
            "published_at": "2026-02-22T00:00:00Z",
        },
        routing={"pipeline": "youtube_tutorial_explainer", "priority": "urgent"},
        metadata={"source_adapter": "contract-test"},
    )


def test_csi_and_ua_event_core_fields_match():
    from universal_agent.signals_ingest import CreatorSignalEvent as UAEvent

    csi_fields = set(CreatorSignalEvent.model_fields.keys())
    ua_fields = set(UAEvent.model_fields.keys())

    # CSI->UA contract requires UA parser to understand all CSI event fields.
    assert csi_fields == ua_fields


def test_signature_generated_by_csi_is_accepted_by_ua(monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")

    batch = CSIIngestBatch(
        csi_version="1.0.0",
        csi_instance_id="csi-vps-01",
        batch_id="batch_001",
        events=[_sample_event()],
    )
    payload = batch.model_dump()
    request_id = "req-1"
    signature_hex, timestamp = generate_signature("secret", request_id, payload)
    headers = {
        "authorization": "Bearer secret",
        "x-csi-request-id": request_id,
        "x-csi-timestamp": timestamp,
        "x-csi-signature": f"sha256={signature_hex}",
    }

    status_code, body = process_signals_ingest_payload(payload, headers)
    assert status_code == 200
    assert body["accepted"] == 1
    assert body["rejected"] == 0


def test_csi_batch_size_limit_enforced():
    events = [_sample_event() for _ in range(101)]
    with pytest.raises(Exception):
        # Explicitly assert pydantic constraint remains active.
        CSIIngestBatch(
            csi_version="1.0.0",
            csi_instance_id="csi-vps-01",
            batch_id="batch_overflow",
            events=events,
        )

