from __future__ import annotations

from types import SimpleNamespace

from universal_agent.gateway_server import (
    _build_csi_recommendation_task_items,
    _classify_csi_recommendation_owner,
    _extract_csi_recommendations,
    _csi_event_notification_policy,
    _csi_task_routing_decision,
    _should_enqueue_csi_task,
    classify_csi_project_key,
)
from universal_agent import task_hub


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


def test_csi_event_notification_policy_syncs_quality_gate_alert_only(monkeypatch):
    monkeypatch.setenv("UA_CSI_RSS_QUALITY_ESCALATION_THRESHOLD", "3")
    alert_policy = _csi_event_notification_policy(
        SimpleNamespace(event_type="rss_quality_gate_alert", subject={"status": "alert"})
    )
    escalated_alert_policy = _csi_event_notification_policy(
        SimpleNamespace(
            event_type="rss_quality_gate_alert",
            subject={"status": "alert", "consecutive_alerts": 3, "transcript_ok_recent": 0},
        )
    )
    ok_policy = _csi_event_notification_policy(
        SimpleNamespace(event_type="rss_quality_gate_ok", subject={"status": "ok"})
    )

    assert alert_policy["todoist_sync"] is True
    assert alert_policy["escalates_to_ua"] is False
    assert alert_policy["requires_action"] is False
    assert escalated_alert_policy["todoist_sync"] is True
    assert escalated_alert_policy["escalates_to_ua"] is True
    assert escalated_alert_policy["requires_action"] is True
    assert ok_policy["todoist_sync"] is False


def test_opportunity_bundle_escalates_only_with_actionable_evidence(monkeypatch):
    monkeypatch.setenv("UA_CSI_OPPORTUNITY_MIN_COUNT", "1")
    monkeypatch.setenv("UA_CSI_OPPORTUNITY_MIN_SIGNAL_VOLUME", "5")
    monkeypatch.setenv("UA_CSI_OPPORTUNITY_MAX_FRESHNESS_MINUTES", "240")
    monkeypatch.setenv("UA_CSI_OPPORTUNITY_MIN_COVERAGE_SCORE", "0.5")

    actionable = _csi_event_notification_policy(
        SimpleNamespace(
            event_type="opportunity_bundle_ready",
            subject={
                "quality_summary": {"signal_volume": 21, "freshness_minutes": 30, "coverage_score": 0.86},
                "opportunities": [{"opportunity_id": "opp-1"}, {"opportunity_id": "opp-2"}],
            },
        )
    )
    low_signal = _csi_event_notification_policy(
        SimpleNamespace(
            event_type="opportunity_bundle_ready",
            subject={
                "quality_summary": {"signal_volume": 1, "freshness_minutes": 20, "coverage_score": 0.42},
                "opportunities": [],
            },
        )
    )
    stale = _csi_event_notification_policy(
        SimpleNamespace(
            event_type="opportunity_bundle_ready",
            subject={
                "quality_summary": {"signal_volume": 12, "freshness_minutes": 480, "coverage_score": 0.8},
                "opportunities": [{"opportunity_id": "opp-1"}],
            },
        )
    )

    assert actionable["escalates_to_ua"] is True
    assert actionable["requires_action"] is True
    assert low_signal["escalates_to_ua"] is False
    assert low_signal["requires_action"] is False
    assert stale["escalates_to_ua"] is False
    assert stale["requires_action"] is False


def test_csi_task_enqueue_default_proactive_mode(monkeypatch):
    monkeypatch.delenv("UA_TASK_HUB_CSI_MODE", raising=False)
    policy = {"has_anomaly": False, "requires_action": True, "escalates_to_ua": True}
    assert _should_enqueue_csi_task(event_type="opportunity_bundle_ready", policy=policy) is True
    assert _should_enqueue_csi_task(event_type="report_product_ready", policy=policy) is False
    assert _should_enqueue_csi_task(
        event_type="opportunity_bundle_ready",
        policy={"has_anomaly": False, "requires_action": True, "escalates_to_ua": False},
    ) is False
    # Strict proactive mode should not enqueue unknown event types even when policy flags anomaly.
    assert _should_enqueue_csi_task(
        event_type="hourly_token_usage_report",
        policy={"has_anomaly": True, "requires_action": True, "escalates_to_ua": True},
    ) is False


def test_csi_task_enqueue_actionable_mode(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_CSI_MODE", "actionable")
    assert _should_enqueue_csi_task(
        event_type="delivery_reliability_slo_breached",
        policy={"has_anomaly": True, "requires_action": True, "escalates_to_ua": True},
    ) is True
    assert _should_enqueue_csi_task(
        event_type="delivery_reliability_slo_breached",
        policy={"has_anomaly": True, "requires_action": True, "escalates_to_ua": False},
    ) is False
    assert _should_enqueue_csi_task(
        event_type="delivery_reliability_slo_breached",
        policy={"has_anomaly": True, "requires_action": False, "escalates_to_ua": True},
    ) is False


def test_csi_task_enqueue_anomalies_only_mode(monkeypatch):
    monkeypatch.setenv("UA_TASK_HUB_CSI_MODE", "anomalies_only")
    assert _should_enqueue_csi_task(
        event_type="delivery_health_regression",
        policy={"has_anomaly": True, "requires_action": True, "escalates_to_ua": True},
    ) is True
    assert _should_enqueue_csi_task(
        event_type="opportunity_bundle_ready",
        policy={"has_anomaly": False, "requires_action": True, "escalates_to_ua": True},
    ) is False


def test_csi_task_routing_defaults_proactive_opportunity_to_incubating():
    routing = _csi_task_routing_decision(
        event_type="opportunity_bundle_ready",
        subject_obj={},
        policy={"escalates_to_ua": True, "requires_action": True},
        loop_state={"status": "open", "confidence_target": 0.72, "confidence_score": 0.6, "follow_up_budget_remaining": 2},
    )
    assert routing["routing_state"] == task_hub.CSI_ROUTING_INCUBATING


def test_csi_task_routing_marks_closed_confident_opportunity_actionable():
    routing = _csi_task_routing_decision(
        event_type="opportunity_bundle_ready",
        subject_obj={},
        policy={"escalates_to_ua": True, "requires_action": True},
        loop_state={
            "status": "closed",
            "confidence_target": 0.72,
            "confidence_score": 0.8,
            "follow_up_budget_remaining": 1,
            "events_count": 2,
        },
    )
    assert routing["routing_state"] == task_hub.CSI_ROUTING_AGENT_ACTIONABLE


def test_csi_task_routing_marks_review_due_human_required():
    routing = _csi_task_routing_decision(
        event_type="csi_global_brief_review_due",
        subject_obj={},
        policy={"escalates_to_ua": True, "requires_action": True},
        loop_state=None,
    )
    assert routing["routing_state"] == task_hub.CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED
    assert _should_enqueue_csi_task(
        event_type="delivery_health_regression",
        policy={"has_anomaly": True, "requires_action": True, "escalates_to_ua": False},
    ) is False


def test_extract_csi_recommendations_includes_subject_and_root_cause_sources():
    subject = {
        "recommendations": [
            "Install pydantic in CSI_Ingester env for auto-remediation",
            {"action": "Fix hook signature mismatch in AgentHookSet.on_pre_compact_capture()"},
        ],
        "top_root_causes": [
            {
                "title": "Replay DLQ after remediation",
                "runbook_command": "python3 scripts/csi_replay_dlq.py --limit 100",
            }
        ],
    }

    items = _extract_csi_recommendations(subject)
    texts = {str(item.get("text") or "") for item in items}
    assert any("Install pydantic" in text for text in texts)
    assert any("Fix hook signature mismatch" in text for text in texts)
    assert any("csi_replay_dlq.py" in text for text in texts)


def test_classify_csi_recommendation_owner_routes_agent_vs_human():
    agent = _classify_csi_recommendation_owner(
        "Install pydantic in CSI_Ingester env for auto-remediation"
    )
    human = _classify_csi_recommendation_owner(
        "Ask Kevin to approve the budget and sign off on remediation"
    )

    assert agent["owner_lane"] == "agent"
    assert human["owner_lane"] == "human"


def test_build_csi_recommendation_task_items_assigns_human_and_agent_queues():
    subject = {
        "recommendations": [
            "Install pydantic in CSI_Ingester env for auto-remediation",
            "Ask Kevin to approve budget before production rollout",
        ]
    }
    items = _build_csi_recommendation_task_items(
        subject=subject,
        event_type="delivery_reliability_slo_breached",
        parent_task_id="csi:delivery_reliability_slo_breached:csi_analytics",
        parent_title="CSI Reliability SLO Breached",
        project_key="immediate",
        must_complete=True,
    )
    assert len(items) == 2
    agent_items = [item for item in items if bool(item.get("agent_ready"))]
    human_items = [item for item in items if not bool(item.get("agent_ready"))]
    assert len(agent_items) == 1
    assert len(human_items) == 1
    assert "agent-ready" in set(agent_items[0].get("labels") or [])
    assert "needs-human" in set(human_items[0].get("labels") or [])
