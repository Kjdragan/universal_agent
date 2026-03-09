from __future__ import annotations

from types import SimpleNamespace

from universal_agent.gateway_server import (
    _csi_event_notification_policy,
    _should_enqueue_csi_task,
    classify_csi_project_key,
)


def test_classify_csi_project_key_immediate_for_regression():
    project = classify_csi_project_key(
        event_type="delivery_health_regression",
        subject={"status": "failing"},
        quality=None,
        source_mix={"csi_analytics": 1},
    )
    assert project == "immediate"


def test_classify_csi_project_key_memory_for_memory_signal():
    project = classify_csi_project_key(
        event_type="report_product_ready",
        subject={"summary": "Memory profile gap detected for creator context"},
        quality=None,
        source_mix={"csi_analytics": 1},
    )
    assert project == "memory"


def test_classify_csi_project_key_mission_for_high_mission_score():
    project = classify_csi_project_key(
        event_type="opportunity_bundle_ready",
        subject={"mission_relevance_score": 0.91, "title": "Mission leverage opportunity"},
        quality=None,
        source_mix={"youtube_channel_rss": 4, "reddit_discovery": 3},
    )
    assert project == "mission"


def test_classify_csi_project_key_proactive_for_quality_alert():
    project = classify_csi_project_key(
        event_type="rss_quality_gate_alert",
        subject={"report_key": "rss:quality:alert"},
        quality={"status": "warning"},
        source_mix={"youtube_channel_rss": 3},
    )
    assert project == "proactive"


def test_classify_csi_project_key_defaults_to_csi():
    project = classify_csi_project_key(
        event_type="report_product_ready",
        subject={"report_key": "hourly:standard"},
        quality={"score": 0.82},
        source_mix={"youtube_channel_rss": 2},
    )
    assert project == "csi"


def test_csi_event_notification_policy_syncs_quality_gate_alert_only():
    alert_policy = _csi_event_notification_policy(
        SimpleNamespace(event_type="rss_quality_gate_alert", subject={"status": "alert"})
    )
    ok_policy = _csi_event_notification_policy(
        SimpleNamespace(event_type="rss_quality_gate_ok", subject={"status": "ok"})
    )

    assert alert_policy["todoist_sync"] is True
    assert ok_policy["todoist_sync"] is False


def test_csi_task_enqueue_default_proactive_mode(monkeypatch):
    monkeypatch.delenv("UA_TASK_HUB_CSI_MODE", raising=False)
    policy = {"has_anomaly": False, "requires_action": True}
    assert _should_enqueue_csi_task(event_type="opportunity_bundle_ready", policy=policy) is True
    assert _should_enqueue_csi_task(event_type="report_product_ready", policy=policy) is False


def test_csi_task_enqueue_actionable_mode(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_CSI_MODE", "actionable")
    assert _should_enqueue_csi_task(
        event_type="report_product_ready",
        policy={"has_anomaly": False, "requires_action": True},
    ) is True
    assert _should_enqueue_csi_task(
        event_type="report_product_ready",
        policy={"has_anomaly": False, "requires_action": False},
    ) is False


def test_csi_task_enqueue_anomalies_only_mode(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_CSI_MODE", "anomalies_only")
    assert _should_enqueue_csi_task(
        event_type="delivery_health_regression",
        policy={"has_anomaly": True, "requires_action": True},
    ) is True
    assert _should_enqueue_csi_task(
        event_type="opportunity_bundle_ready",
        policy={"has_anomaly": False, "requires_action": True},
    ) is False
