from __future__ import annotations

import asyncio

import httpx
import pytest

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import (
    UAEmitter,
    classify_emission_failure,
    is_transient_failure,
)


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


# -------------------------------------------------------------------
# classify_emission_failure unit tests
# -------------------------------------------------------------------


class TestClassifyEmissionFailure:
    def test_maintenance_mode(self):
        assert classify_emission_failure(maintenance_mode=True) == "maintenance_mode"

    def test_connect_error(self):
        exc = httpx.ConnectError("connection refused")
        assert classify_emission_failure(exc=exc) == "transient_connection"

    def test_timeout_exception(self):
        exc = httpx.ReadTimeout("read timed out")
        assert classify_emission_failure(exc=exc) == "transient_timeout"

    def test_asyncio_timeout(self):
        exc = asyncio.TimeoutError()
        assert classify_emission_failure(exc=exc) == "transient_timeout"

    def test_generic_exception(self):
        exc = RuntimeError("something unexpected")
        assert classify_emission_failure(exc=exc) == "unknown"

    def test_transient_http_502(self):
        assert classify_emission_failure(status_code=502) == "transient_server"

    def test_transient_http_503(self):
        assert classify_emission_failure(status_code=503) == "transient_server"

    def test_transient_http_504(self):
        assert classify_emission_failure(status_code=504) == "transient_server"

    def test_rate_limit_429(self):
        assert classify_emission_failure(status_code=429) == "transient_rate_limit"

    def test_permanent_400(self):
        assert classify_emission_failure(status_code=400) == "permanent_client_error"

    def test_permanent_401(self):
        assert classify_emission_failure(status_code=401) == "permanent_client_error"

    def test_permanent_403(self):
        assert classify_emission_failure(status_code=403) == "permanent_client_error"

    def test_permanent_404(self):
        assert classify_emission_failure(status_code=404) == "permanent_client_error"

    def test_status_zero(self):
        assert classify_emission_failure(status_code=0) == "transient_connection"

    def test_unknown_status(self):
        assert classify_emission_failure(status_code=500) == "unknown"


class TestIsTransientFailure:
    def test_transient_connection(self):
        assert is_transient_failure("transient_connection") is True

    def test_transient_timeout(self):
        assert is_transient_failure("transient_timeout") is True

    def test_transient_server(self):
        assert is_transient_failure("transient_server") is True

    def test_maintenance_mode(self):
        assert is_transient_failure("maintenance_mode") is True

    def test_permanent(self):
        assert is_transient_failure("permanent_client_error") is False

    def test_unknown(self):
        assert is_transient_failure("unknown") is False


# -------------------------------------------------------------------
# emit_batch error handling tests
# -------------------------------------------------------------------


async def test_emit_batch_connect_error_classified(monkeypatch):
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")

    async def _raise_connect(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", _raise_connect)
    status, body = await emitter.emit_batch([_event()])
    assert status == 0
    assert body["failure_class"] == "transient_connection"
    assert body["ok"] is False


async def test_emit_batch_timeout_classified(monkeypatch):
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")

    async def _raise_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(httpx.AsyncClient, "post", _raise_timeout)
    status, body = await emitter.emit_batch([_event()])
    assert status == 0
    assert body["failure_class"] == "transient_timeout"
    assert body["ok"] is False


# -------------------------------------------------------------------
# emit_with_retries tests (original + new)
# -------------------------------------------------------------------


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
    assert body.get("failure_class") == "permanent_client_error"


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


async def test_emit_with_retries_maintenance_mode_skips_network():
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")
    ok, status, body = await emitter.emit_with_retries([_event()], maintenance_mode=True)
    assert ok is False
    assert status == 0
    assert body["failure_class"] == "maintenance_mode"


async def test_emit_with_retries_transient_uses_exponential_backoff(monkeypatch):
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")
    calls = {"n": 0}
    sleep_durations: list[float] = []

    async def _emit_batch(events, timeout_seconds=30):
        calls["n"] += 1
        return 0, {"ok": False, "failure_class": "transient_connection", "error": "ConnectError"}

    async def _track_sleep(duration):
        sleep_durations.append(duration)

    monkeypatch.setattr(emitter, "emit_batch", _emit_batch)
    monkeypatch.setattr("csi_ingester.emitter.ua_client.asyncio.sleep", _track_sleep)
    ok, status, body = await emitter.emit_with_retries([_event()], max_attempts=4)
    assert ok is False
    assert calls["n"] == 4
    assert len(sleep_durations) == 3
    # Delays should increase (exponential backoff)
    assert sleep_durations[0] < sleep_durations[1] < sleep_durations[2]
    assert body["failure_class"] == "transient_connection"


async def test_emit_with_retries_all_503_returns_failure_class(monkeypatch):
    emitter = UAEmitter(endpoint="http://example.test", shared_secret="secret", instance_id="csi-test")

    async def _emit_batch(events, timeout_seconds=30):
        return 503, {"ok": False}

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(emitter, "emit_batch", _emit_batch)
    monkeypatch.setattr("csi_ingester.emitter.ua_client.asyncio.sleep", _no_sleep)
    ok, status, body = await emitter.emit_with_retries([_event()], max_attempts=3)
    assert ok is False
    assert status == 503
    assert body["failure_class"] == "transient_server"

