import hashlib
import hmac
import json
import time

from universal_agent.signals_ingest import (
    extract_valid_events,
    process_signals_ingest_payload,
    to_csi_analytics_action,
    to_manual_youtube_payload,
)


def _sign(secret: str, request_id: str, timestamp: str, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    signing_string = f"{timestamp}.{request_id}.{body}"
    digest = hmac.new(secret.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _valid_payload() -> dict:
    return {
        "csi_version": "1.0.0",
        "csi_instance_id": "csi-vps-01",
        "batch_id": "batch_01",
        "events": [
            {
                "event_id": "evt-1",
                "dedupe_key": "dedupe-1",
                "source": "youtube_playlist",
                "event_type": "video_added_to_playlist",
                "occurred_at": "2026-02-22T00:00:00Z",
                "received_at": "2026-02-22T00:00:02Z",
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


def test_signals_ingest_disabled(monkeypatch):
    monkeypatch.delenv("UA_SIGNALS_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("UA_SIGNALS_INGEST_SHARED_SECRET", raising=False)
    status_code, body = process_signals_ingest_payload(_valid_payload(), {})
    assert status_code == 503
    assert body["error"] == "signals_ingest_disabled"


def test_signals_ingest_unauthorized(monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    status_code, body = process_signals_ingest_payload(_valid_payload(), {})
    assert status_code == 401
    assert body["error"] == "unauthorized"


def test_signals_ingest_success(monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    payload = _valid_payload()
    request_id = "req-1"
    timestamp = str(int(time.time()))
    headers = {
        "authorization": "Bearer secret",
        "x-csi-request-id": request_id,
        "x-csi-timestamp": timestamp,
        "x-csi-signature": _sign("secret", request_id, timestamp, payload),
    }
    status_code, body = process_signals_ingest_payload(payload, headers)
    assert status_code == 200
    assert body["accepted"] == 1
    assert body["rejected"] == 0


def test_signals_ingest_partial_failure(monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    payload = _valid_payload()
    payload["events"].append({"event_id": "evt-2"})  # invalid schema
    request_id = "req-2"
    timestamp = str(int(time.time()))
    headers = {
        "authorization": "Bearer secret",
        "x-csi-request-id": request_id,
        "x-csi-timestamp": timestamp,
        "x-csi-signature": _sign("secret", request_id, timestamp, payload),
    }
    status_code, body = process_signals_ingest_payload(payload, headers)
    assert status_code == 207
    assert body["accepted"] == 1
    assert body["rejected"] == 1


def test_signals_ingest_instance_not_allowed(monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "other-instance")
    payload = _valid_payload()
    request_id = "req-3"
    timestamp = str(int(time.time()))
    headers = {
        "authorization": "Bearer secret",
        "x-csi-request-id": request_id,
        "x-csi-timestamp": timestamp,
        "x-csi-signature": _sign("secret", request_id, timestamp, payload),
    }
    status_code, body = process_signals_ingest_payload(payload, headers)
    assert status_code == 403
    assert body["error"] == "instance_not_allowed"


def test_signals_ingest_signature_mismatch(monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    payload = _valid_payload()
    request_id = "req-4"
    timestamp = str(int(time.time()))
    headers = {
        "authorization": "Bearer secret",
        "x-csi-request-id": request_id,
        "x-csi-timestamp": timestamp,
        "x-csi-signature": "sha256=deadbeef",
    }
    status_code, body = process_signals_ingest_payload(payload, headers)
    assert status_code == 401
    assert body["error"] == "unauthorized"


def test_extract_valid_events_returns_only_valid():
    payload = _valid_payload()
    payload["events"].append({"event_id": "bad"})
    valid = extract_valid_events(payload)
    assert len(valid) == 1
    assert valid[0].event_id == "evt-1"


def test_to_manual_youtube_payload_maps_youtube_event():
    event = extract_valid_events(_valid_payload())[0]
    mapped = to_manual_youtube_payload(event)
    assert mapped is not None
    assert mapped["video_id"] == "dQw4w9WgXcQ"
    assert mapped["mode"] == "explainer_plus_code"


def test_to_manual_youtube_payload_skips_rss_event():
    payload = _valid_payload()
    payload["events"][0]["source"] = "youtube_channel_rss"
    event = extract_valid_events(payload)[0]
    mapped = to_manual_youtube_payload(event)
    assert mapped is None


def test_to_csi_analytics_action_maps_trend_report():
    payload = _valid_payload()
    payload["events"][0]["source"] = "csi_analytics"
    payload["events"][0]["event_type"] = "rss_trend_report"
    payload["events"][0]["subject"] = {
        "window_start_utc": "2026-02-22T00:00:00Z",
        "window_end_utc": "2026-02-22T01:00:00Z",
        "totals": {"items": 4, "by_category": {"ai": 3, "other_interest": 1}},
    }
    event = extract_valid_events(payload)[0]
    action = to_csi_analytics_action(event)
    assert action is not None
    assert action["to"] == "trend-specialist"
    assert action["session_key"] == "csi_trend_specialist"
    assert "rss_trend_report" in action["message"]


def test_to_csi_analytics_action_routes_token_report_to_data_analyst():
    payload = _valid_payload()
    payload["events"][0]["source"] = "csi_analytics"
    payload["events"][0]["event_type"] = "hourly_token_usage_report"
    payload["events"][0]["subject"] = {"totals": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    event = extract_valid_events(payload)[0]
    action = to_csi_analytics_action(event)
    assert action is not None
    assert action["to"] == "data-analyst"


def test_to_csi_analytics_action_skips_playlist_source():
    event = extract_valid_events(_valid_payload())[0]
    action = to_csi_analytics_action(event)
    assert action is None
