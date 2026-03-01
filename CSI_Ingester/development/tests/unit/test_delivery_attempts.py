from __future__ import annotations

import sqlite3

from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import CSIConfig
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.store.sqlite import ensure_schema


def _event(event_id: str = "evt-test-1") -> CreatorSignalEvent:
    return CreatorSignalEvent(
        event_id=event_id,
        dedupe_key=f"dedupe:{event_id}",
        source="csi_analytics",
        event_type="rss_trend_report",
        occurred_at="2026-03-01T00:00:00Z",
        received_at="2026-03-01T00:00:01Z",
        subject={"report_key": "rss:test"},
        routing={"pipeline": "creator_watchlist_handler", "priority": "standard"},
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def test_emit_and_track_records_not_configured_delivery_attempt_and_dlq():
    conn = _conn()
    config = CSIConfig(raw={"csi": {"instance_id": "csi-test"}, "delivery": {}})

    delivered, status_code, payload = emit_and_track(conn, config=config, event=_event("evt-noconfig"), retry_count=3)
    assert delivered is False
    assert status_code == 503
    assert payload.get("error") == "ua_delivery_not_configured"

    attempt = conn.execute(
        "SELECT delivered, status_code, error_class FROM delivery_attempts WHERE event_id = ?",
        ("evt-noconfig",),
    ).fetchone()
    assert attempt is not None
    assert int(attempt["delivered"]) == 0
    assert int(attempt["status_code"]) == 503
    assert str(attempt["error_class"]) == "upstream_5xx"

    dlq = conn.execute(
        "SELECT error_reason FROM dead_letter WHERE event_id = ?",
        ("evt-noconfig",),
    ).fetchone()
    assert dlq is not None
    assert str(dlq["error_reason"]) == "ua_delivery_not_configured"


def test_emit_and_track_records_emit_exception_delivery_attempt(monkeypatch):
    conn = _conn()
    config = CSIConfig(
        raw={
            "csi": {"instance_id": "csi-test"},
            "delivery": {"ua_endpoint": "http://example.invalid/api/v1/signals/ingest"},
        }
    )
    monkeypatch.setenv("CSI_UA_SHARED_SECRET", "test-secret")

    async def _boom(self, events, max_attempts=3):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(UAEmitter, "emit_with_retries", _boom)

    delivered, status_code, _payload = emit_and_track(conn, config=config, event=_event("evt-timeout"), retry_count=3)
    assert delivered is False
    assert status_code == 599

    attempt = conn.execute(
        "SELECT delivered, status_code, error_class FROM delivery_attempts WHERE event_id = ?",
        ("evt-timeout",),
    ).fetchone()
    assert attempt is not None
    assert int(attempt["delivered"]) == 0
    assert int(attempt["status_code"]) == 599
    assert str(attempt["error_class"]) in {"timeouterror", "timeout"}

    dlq = conn.execute(
        "SELECT error_reason FROM dead_letter WHERE event_id = ?",
        ("evt-timeout",),
    ).fetchone()
    assert dlq is not None
    assert str(dlq["error_reason"]) == "ua_status_599"

