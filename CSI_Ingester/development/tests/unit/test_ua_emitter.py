from __future__ import annotations

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter


def _event() -> CreatorSignalEvent:
    return CreatorSignalEvent(
        event_id="evt-1",
        dedupe_key="dedupe-1",
        source="youtube_playlist",
        event_type="video_added_to_playlist",
        occurred_at="2026-02-22T00:00:00Z",
        received_at="2026-02-22T00:00:01Z",
        subject={
            "platform": "youtube",
            "video_id": "abc",
            "channel_id": "UC1",
            "url": "https://youtube.com/watch?v=abc",
            "title": "Test",
            "published_at": "2026-02-22T00:00:00Z",
        },
        routing={"pipeline": "youtube_tutorial_explainer", "priority": "urgent"},
    )


async def test_emit_with_retries_success(monkeypatch):
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")
    calls = {"n": 0}

    async def _emit_batch(events, timeout_seconds=30):
        calls["n"] += 1
        return 200, {"ok": True}

    monkeypatch.setattr(emitter, "emit_batch", _emit_batch)
    ok, status, body = await emitter.emit_with_retries([_event()])
    assert ok is True
    assert status == 200
    assert calls["n"] == 1
    assert body["ok"] is True


async def test_emit_with_retries_permanent_failure(monkeypatch):
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")
    calls = {"n": 0}

    async def _emit_batch(events, timeout_seconds=30):
        calls["n"] += 1
        return 401, {"ok": False}

    monkeypatch.setattr(emitter, "emit_batch", _emit_batch)
    ok, status, body = await emitter.emit_with_retries([_event()])
    assert ok is False
    assert status == 401
    assert calls["n"] == 1
    assert body["ok"] is False


async def test_emit_with_retries_recovers_after_retry(monkeypatch):
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")
    responses = [(503, {"ok": False}), (200, {"ok": True})]

    async def _emit_batch(events, timeout_seconds=30):
        return responses.pop(0)

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(emitter, "emit_batch", _emit_batch)
    monkeypatch.setattr("csi_ingester.emitter.ua_client.asyncio.sleep", _no_sleep)
    ok, status, body = await emitter.emit_with_retries([_event()])
    assert ok is True
    assert status == 200
    assert body["ok"] is True

