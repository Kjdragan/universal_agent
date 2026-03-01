import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
import pytest

from universal_agent import gateway_server
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
        self.action_calls = []

    async def dispatch_internal_payload(self, *, subpath, payload, headers=None):
        self.calls.append({"subpath": subpath, "payload": payload, "headers": headers or {}})
        return True, "agent"

    async def dispatch_internal_action(self, action_payload):
        self.action_calls.append(action_payload)
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
    assert hook_stub.action_calls == []


def test_signals_ingest_csi_analytics_dispatches_internal_action(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "rss_trend_report"
    payload["events"][0]["subject"] = {
        "window_start_utc": "2026-02-22T00:00:00Z",
        "window_end_utc": "2026-02-22T01:00:00Z",
        "totals": {"items": 2, "by_category": {"ai": 2}},
    }
    request_id = "req-4"
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
    assert body["analytics_internal_dispatches"] == 1
    assert hook_stub.calls == []
    assert len(hook_stub.action_calls) >= 1
    assert hook_stub.action_calls[0]["to"] == "trend-specialist"
    assert hook_stub.action_calls[0]["session_key"] == "csi_trend_specialist"


def test_signals_ingest_csi_analytics_throttles_noisy_events(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setenv("UA_CSI_RSS_INSIGHT_EMERGING_COOLDOWN_SECONDS", "3600")
    monkeypatch.setattr(gateway_server, "_csi_dispatch_recent", {})
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "rss_insight_emerging"
    payload["events"][0]["subject"] = {
        "report_key": "rss:emerging:hourly",
        "total_items": 4,
    }

    request_id_1 = "req-throttle-1"
    timestamp_1 = str(int(time.time()))
    headers_1 = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id_1,
        "X-CSI-Timestamp": timestamp_1,
        "X-CSI-Signature": _sign("secret", request_id_1, timestamp_1, payload),
    }
    response_1 = client.post("/api/v1/signals/ingest", json=payload, headers=headers_1)
    assert response_1.status_code == 200
    body_1 = response_1.json()
    assert body_1["analytics_internal_dispatches"] == 1

    request_id_2 = "req-throttle-2"
    timestamp_2 = str(int(time.time()))
    headers_2 = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id_2,
        "X-CSI-Timestamp": timestamp_2,
        "X-CSI-Signature": _sign("secret", request_id_2, timestamp_2, payload),
    }
    response_2 = client.post("/api/v1/signals/ingest", json=payload, headers=headers_2)
    assert response_2.status_code == 200
    body_2 = response_2.json()
    assert body_2["accepted"] == 1
    assert body_2.get("analytics_internal_dispatches", 0) == 0
    assert body_2["analytics_throttled"] == 1
    primary_actions = [call for call in hook_stub.action_calls if str(call.get("name") or "") == "CSIAnalyticsEvent"]
    assert len(primary_actions) == 1


def test_signals_ingest_missing_todoist_credentials_is_notice_not_error(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    monkeypatch.delenv("TODOIST_API_KEY", raising=False)
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "rss_trend_report"
    payload["events"][0]["subject"] = {
        "window_start_utc": "2026-02-22T00:00:00Z",
        "window_end_utc": "2026-02-22T01:00:00Z",
        "totals": {"items": 2, "by_category": {"ai": 2}},
    }
    request_id = "req-5"
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
    assert body["analytics_internal_dispatches"] == 1
    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "system_error" not in kinds
    assert "system_notice" in kinds
    notice = next(item for item in gateway_server._notifications if item.get("kind") == "system_notice")
    assert notice.get("title") == "Todoist Sync Skipped"


def test_signals_ingest_keeps_full_csi_notification_message(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "rss_trend_report"
    payload["events"][0]["subject"] = {
        "window_start_utc": "2026-02-22T00:00:00Z",
        "window_end_utc": "2026-02-22T01:00:00Z",
        "totals": {"items": 2, "by_category": {"ai": 2}},
    }
    request_id = "req-full-message"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    csi_notice = next(item for item in gateway_server._notifications if item.get("kind") == "csi_insight")
    assert "subject_json:" in str(csi_notice.get("message") or "")
    assert not str(csi_notice.get("message") or "").endswith("...")


def test_signals_ingest_delivery_health_regression_emits_actionable_alert(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "delivery_health_regression"
    payload["events"][0]["subject"] = {
        "status": "failing",
        "failing_sources": ["youtube_channel_rss"],
        "degraded_sources": [],
        "remediation": {
            "steps": [
                {
                    "code": "delivery_failures_detected",
                    "source": "youtube_channel_rss",
                    "runbook_command": "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_replay_dlq.py --limit 100",
                }
            ]
        },
    }
    request_id = "req-delivery-health-regression"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    regression_notice = next(
        item for item in gateway_server._notifications if item.get("kind") == "csi_delivery_health_regression"
    )
    assert regression_notice.get("severity") == "error"
    assert bool(regression_notice.get("requires_action")) is True
    metadata = regression_notice.get("metadata") if isinstance(regression_notice.get("metadata"), dict) else {}
    assert metadata.get("delivery_health_status") == "failing"
    assert isinstance(metadata.get("remediation_steps"), list)
    assert "csi_replay_dlq.py" in str(metadata.get("primary_runbook_command") or "")


def test_signals_ingest_delivery_health_recovered_emits_success_notice(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "delivery_health_recovered"
    payload["events"][0]["subject"] = {
        "status": "ok",
        "failing_sources": [],
        "degraded_sources": [],
        "remediation": {"steps": []},
    }
    request_id = "req-delivery-health-recovered"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    recovered_notice = next(
        item for item in gateway_server._notifications if item.get("kind") == "csi_delivery_health_recovered"
    )
    assert recovered_notice.get("severity") == "success"
    assert bool(recovered_notice.get("requires_action")) is False


def test_signals_ingest_reliability_slo_breached_emits_actionable_notice(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "delivery_reliability_slo_breached"
    payload["events"][0]["subject"] = {
        "status": "breached",
        "target_day_utc": "2026-03-01",
        "metrics": {"delivery_success_ratio": 0.71, "dlq_backlog_current": 8, "canary_regression_count": 4},
        "thresholds": {"min_delivery_success_ratio": 0.98, "max_dlq_backlog": 0, "max_canary_regressions": 2},
        "top_root_causes": [
            {
                "code": "delivery_success_ratio_below_min",
                "title": "Delivery success ratio below SLO",
                "runbook_command": "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_replay_dlq.py --limit 200",
            }
        ],
    }
    request_id = "req-reliability-slo-breached"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    notice = next(
        item for item in gateway_server._notifications if item.get("kind") == "csi_delivery_reliability_slo_breached"
    )
    assert notice.get("severity") == "error"
    assert bool(notice.get("requires_action")) is True
    metadata = notice.get("metadata") if isinstance(notice.get("metadata"), dict) else {}
    assert metadata.get("slo_status") == "breached"
    assert isinstance(metadata.get("top_root_causes"), list)
    assert "csi_replay_dlq.py" in str(metadata.get("primary_runbook_command") or "")


def test_signals_ingest_reliability_slo_recovered_emits_success_notice(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "delivery_reliability_slo_recovered"
    payload["events"][0]["subject"] = {
        "status": "ok",
        "target_day_utc": "2026-03-02",
        "metrics": {"delivery_success_ratio": 1.0, "dlq_backlog_current": 0, "canary_regression_count": 0},
        "thresholds": {"min_delivery_success_ratio": 0.98, "max_dlq_backlog": 0, "max_canary_regressions": 2},
        "top_root_causes": [],
    }
    request_id = "req-reliability-slo-recovered"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    notice = next(
        item for item in gateway_server._notifications if item.get("kind") == "csi_delivery_reliability_slo_recovered"
    )
    assert notice.get("severity") == "success"
    assert bool(notice.get("requires_action")) is False


def test_signals_ingest_auto_remediation_failed_emits_actionable_notice(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "delivery_health_auto_remediation_failed"
    payload["events"][0]["subject"] = {
        "status": "failed",
        "health_status": "failing",
        "executed_actions": [
            {
                "handler": "restart_ingester",
                "code": "adapter_consecutive_failures",
                "source": "youtube_channel_rss",
                "success": False,
                "result": {"detail": "exit=1 stderr=permission denied"},
            }
        ],
        "skipped_actions": [],
    }
    request_id = "req-auto-remediation-failed"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    notice = next(
        item
        for item in gateway_server._notifications
        if item.get("kind") == "csi_delivery_health_auto_remediation_failed"
    )
    assert len(hook_stub.action_calls) >= 1
    assert str(hook_stub.action_calls[0].get("to") or "") == "data-analyst"
    assert notice.get("severity") == "error"
    assert bool(notice.get("requires_action")) is True
    metadata = notice.get("metadata") if isinstance(notice.get("metadata"), dict) else {}
    assert metadata.get("auto_remediation_status") == "failed"
    assert metadata.get("delivery_health_status") == "failing"
    assert isinstance(metadata.get("executed_actions"), list)


def test_signals_ingest_noisy_events_emit_hourly_digest(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "category_quality_report"
    payload["events"][0]["subject"] = {
        "action": "no_change",
        "metrics": {"total_items": 5, "other_interest_ratio": 0.2, "uncategorized_items": 0},
    }
    request_id = "req-digest"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "csi_pipeline_digest" in kinds
    assert "csi_insight" not in kinds


def test_signals_ingest_emits_specialist_hourly_and_daily_synthesis(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "rss_insight_daily"
    payload["events"][0]["subject"] = {
        "report_type": "rss_insight_daily",
        "report_key": "rss_insight:daily:test",
        "window_start_utc": "2026-02-22T00:00:00Z",
        "window_end_utc": "2026-02-23T00:00:00Z",
        "total_items": 12,
    }
    request_id = "req-specialist"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "csi_insight" in kinds
    assert "csi_specialist_hourly_synthesis" in kinds
    assert "csi_specialist_daily_rollup" in kinds


def test_signals_ingest_digest_event_skips_todoist_notice(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    monkeypatch.delenv("TODOIST_API_KEY", raising=False)
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "hourly_token_usage_report"
    payload["events"][0]["subject"] = {"totals": {"total_tokens": 12345}}
    request_id = "req-digest-skip-todoist"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "csi_pipeline_digest" in kinds
    assert "system_notice" not in kinds


def test_signals_ingest_emerging_requests_followup_and_records_loop(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    unique_suffix = str(int(time.time()))
    payload["events"][0]["event_type"] = "rss_insight_emerging"
    payload["events"][0]["subject"] = {
        "report_type": "rss_insight_emerging",
        "report_key": f"rss_insight:emerging:test:{unique_suffix}",
        "window_start_utc": "2026-02-22T00:00:00Z",
        "window_end_utc": "2026-02-22T06:00:00Z",
        "total_items": 6,
    }
    request_id = "req-loop-followup"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200
    assert len(hook_stub.action_calls) >= 1
    followup_calls = [call for call in hook_stub.action_calls if str(call.get("name") or "") == "CSITrendFollowUpRequest"]
    assert followup_calls

    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "csi_specialist_followup_requested" in kinds or "csi_specialist_confidence_reached" in kinds

    loops_resp = client.get("/api/v1/dashboard/csi/specialist-loops?limit=10")
    assert loops_resp.status_code == 200
    loops = loops_resp.json().get("loops") or []
    assert loops
    assert int(loops[0].get("follow_up_budget_remaining") or 0) <= 2
    assert str(loops[0].get("confidence_method") or "") in {"heuristic", "evidence_model"}


def test_signals_ingest_opportunity_bundle_uses_evidence_confidence(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    unique_suffix = str(int(time.time()))
    payload["events"][0]["event_type"] = "opportunity_bundle_ready"
    payload["events"][0]["subject"] = {
        "report_type": "opportunity_bundle",
        "report_key": f"opportunity_bundle:test:{unique_suffix}",
        "bundle_id": f"bundle:test:{unique_suffix}",
        "quality_summary": {"signal_volume": 21, "freshness_minutes": 24, "coverage_score": 0.86},
        "opportunities": [
            {"opportunity_id": "opp-1", "title": "Momentum theme", "source_mix": {"youtube_channel_rss": 9, "reddit_discovery": 5}},
            {"opportunity_id": "opp-2", "title": "Community pulse", "source_mix": {"reddit_discovery": 6}},
        ],
        "window_start_utc": "2026-02-22T00:00:00Z",
        "window_end_utc": "2026-02-22T06:00:00Z",
    }
    request_id = "req-loop-evidence"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }

    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200

    loops_resp = client.get("/api/v1/dashboard/csi/specialist-loops?limit=10")
    assert loops_resp.status_code == 200
    loops = loops_resp.json().get("loops") or []
    assert loops
    assert str(loops[0].get("confidence_method") or "") == "evidence_model"
    evidence = loops[0].get("confidence_evidence") if isinstance(loops[0].get("confidence_evidence"), dict) else {}
    assert int(evidence.get("signal_volume") or 0) >= 1


def test_signals_ingest_low_signal_suppresses_followup_and_emits_alert(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setenv("UA_CSI_SPECIALIST_MIN_SIGNAL_VOLUME", "5")
    monkeypatch.setenv("UA_CSI_SPECIALIST_LOW_SIGNAL_STREAK_THRESHOLD", "2")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    unique_suffix = str(int(time.time()))
    payload = _payload(source="csi_analytics")
    payload["events"][0]["event_type"] = "opportunity_bundle_ready"
    payload["events"][0]["subject"] = {
        "report_type": "opportunity_bundle",
        "report_key": f"opportunity_bundle:low-signal:{unique_suffix}",
        "bundle_id": f"bundle:low-signal:{unique_suffix}",
        "quality_summary": {"signal_volume": 1, "freshness_minutes": 12, "coverage_score": 0.42},
        "opportunities": [],
    }

    for idx in range(2):
        request_id = f"req-low-signal-{idx}"
        timestamp = str(int(time.time()))
        headers = {
            "Authorization": "Bearer secret",
            "X-CSI-Request-ID": request_id,
            "X-CSI-Timestamp": timestamp,
            "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
        }
        response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
        assert response.status_code == 200

    loops_resp = client.get("/api/v1/dashboard/csi/specialist-loops?limit=10")
    assert loops_resp.status_code == 200
    loops = loops_resp.json().get("loops") or []
    assert loops
    assert str(loops[0].get("status") or "") == "suppressed_low_signal"
    assert int(loops[0].get("low_signal_streak") or 0) >= 2

    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "csi_specialist_low_signal_suppressed" in kinds


def test_signals_ingest_stale_evidence_emits_quality_alert(client, monkeypatch):
    monkeypatch.setenv("UA_SIGNALS_INGEST_ENABLED", "1")
    monkeypatch.setenv("UA_SIGNALS_INGEST_SHARED_SECRET", "secret")
    monkeypatch.setenv("UA_SIGNALS_INGEST_ALLOWED_INSTANCES", "csi-vps-01")
    monkeypatch.setenv("UA_CSI_SPECIALIST_STALE_EVIDENCE_MINUTES", "180")
    monkeypatch.setattr(gateway_server, "_notifications", [])
    hook_stub = _HookStub()
    monkeypatch.setattr("universal_agent.gateway_server._hooks_service", hook_stub)

    payload = _payload(source="csi_analytics")
    unique_suffix = str(int(time.time()))
    payload["events"][0]["event_type"] = "opportunity_bundle_ready"
    payload["events"][0]["subject"] = {
        "report_type": "opportunity_bundle",
        "report_key": f"opportunity_bundle:stale:{unique_suffix}",
        "bundle_id": f"bundle:stale:{unique_suffix}",
        "quality_summary": {"signal_volume": 12, "freshness_minutes": 480, "coverage_score": 0.8},
        "opportunities": [{"opportunity_id": "opp-1", "title": "Stale candidate"}],
    }
    request_id = "req-stale-evidence"
    timestamp = str(int(time.time()))
    headers = {
        "Authorization": "Bearer secret",
        "X-CSI-Request-ID": request_id,
        "X-CSI-Timestamp": timestamp,
        "X-CSI-Signature": _sign("secret", request_id, timestamp, payload),
    }
    response = client.post("/api/v1/signals/ingest", json=payload, headers=headers)
    assert response.status_code == 200

    kinds = [str(item.get("kind") or "") for item in gateway_server._notifications]
    assert "csi_specialist_evidence_stale" in kinds
