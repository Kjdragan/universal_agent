from __future__ import annotations

import sqlite3

import pytest

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store.sqlite import ensure_schema
from csi_ingester.threads_webhooks import (
    ThreadsWebhookEnvelope,
    ThreadsWebhookSettings,
    envelope_to_events,
    ingest_threads_webhook_envelope,
    validate_signed_payload,
    validate_verification_request,
)


def test_threads_webhook_verification_token_guard():
    settings = ThreadsWebhookSettings(enabled=True, verify_token="verify-me", app_secret="secret")
    challenge = validate_verification_request(
        mode="subscribe",
        verify_token="verify-me",
        challenge="abc123",
        settings=settings,
    )
    assert challenge == "abc123"


def test_threads_webhook_signature_validation():
    settings = ThreadsWebhookSettings(enabled=True, verify_token="", app_secret="topsecret")
    payload = b'{"object":"threads"}'

    import hmac
    import hashlib

    digest = hmac.new(b"topsecret", payload, hashlib.sha256).hexdigest()
    assert validate_signed_payload(raw_body=payload, signature_header=f"sha256={digest}", settings=settings) is True


def test_threads_webhook_envelope_to_events_uses_media_dedupe_key():
    envelope = ThreadsWebhookEnvelope.model_validate(
        {
            "object": "threads",
            "entry": [
                {
                    "id": "acct-1",
                    "time": 1772720401,
                    "changes": [
                        {
                            "field": "mentions",
                            "value": {
                                "id": "18000000000000001",
                                "text": "hello from webhook",
                                "username": "waynepainters",
                            },
                        }
                    ],
                }
            ],
        }
    )
    events = envelope_to_events(envelope)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, CreatorSignalEvent)
    assert event.source == "threads_owned"
    assert event.dedupe_key == "threads:18000000000000001"
    assert event.event_type == "threads_mention_observed"


class _FakeEmitter:
    def __init__(self):
        self.calls: list[list[CreatorSignalEvent]] = []

    async def emit_with_retries(self, events):
        self.calls.append(events)
        return True, 200, {"ok": True}


@pytest.mark.asyncio
async def test_threads_webhook_ingest_dedupes_on_repeat():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    emitter = _FakeEmitter()
    envelope = ThreadsWebhookEnvelope.model_validate(
        {
            "object": "threads",
            "entry": [
                {
                    "id": "acct-1",
                    "time": 1772720401,
                    "changes": [
                        {
                            "field": "threads",
                            "value": {
                                "id": "18000000000000002",
                                "text": "first",
                            },
                        }
                    ],
                }
            ],
        }
    )

    first = await ingest_threads_webhook_envelope(conn=conn, envelope=envelope, emitter=emitter)
    second = await ingest_threads_webhook_envelope(conn=conn, envelope=envelope, emitter=emitter)

    assert int(first["stored"]) == 1
    assert int(first["deduped"]) == 0
    assert int(first["delivered"]) == 1
    assert int(second["stored"]) == 0
    assert int(second["deduped"]) == 1
    assert int(second["delivered"]) == 0
