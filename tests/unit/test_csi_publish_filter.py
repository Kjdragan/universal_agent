"""Packet 19: tests for CSI high-signal publishing pipeline."""

import pytest

from universal_agent.csi_publish_filter import (
    classify_publish_tier,
    filter_notifications_for_publish,
    build_executive_digest,
)


class TestClassifyPublishTier:
    def test_delivery_regression_is_critical(self):
        assert classify_publish_tier(kind="csi_delivery_health_regression") == "critical"

    def test_slo_breach_is_critical(self):
        assert classify_publish_tier(kind="csi_reliability_slo_breach") == "critical"

    def test_auto_remediation_failed_is_critical(self):
        assert classify_publish_tier(kind="csi_delivery_health_auto_remediation_failed") == "critical"

    def test_error_severity_is_critical(self):
        assert classify_publish_tier(kind="csi_unknown_kind", severity="error") == "critical"

    def test_critical_severity_is_critical(self):
        assert classify_publish_tier(kind="csi_unknown_kind", severity="critical") == "critical"

    def test_high_quality_report_is_high_value(self):
        tier = classify_publish_tier(
            kind="csi_report_product_ready",
            metadata={"quality": {"quality_score": 0.85, "quality_grade": "A"}},
        )
        assert tier == "high_value"

    def test_low_quality_report_is_digest(self):
        tier = classify_publish_tier(
            kind="csi_report_product_ready",
            metadata={"quality": {"quality_score": 0.3, "quality_grade": "D"}},
        )
        assert tier == "digest"

    def test_report_with_anomaly_flag_is_high_value(self):
        tier = classify_publish_tier(
            kind="csi_report_product_ready",
            metadata={"notification_policy": {"has_anomaly": True}},
        )
        assert tier == "high_value"

    def test_opportunity_bundle_high_quality(self):
        tier = classify_publish_tier(
            kind="csi_opportunity_bundle_ready",
            metadata={"quality": {"quality_score": 0.7, "quality_grade": "B"}},
        )
        assert tier == "high_value"

    def test_recovery_is_digest(self):
        assert classify_publish_tier(kind="csi_delivery_health_recovered") == "digest"

    def test_slo_recovery_is_digest(self):
        assert classify_publish_tier(kind="csi_reliability_slo_recovery") == "digest"

    def test_specialist_synthesis_is_digest(self):
        assert classify_publish_tier(kind="csi_specialist_hourly_synthesis") == "digest"

    def test_trend_report_is_suppressed(self):
        assert classify_publish_tier(kind="csi_rss_trend_report") == "suppressed"

    def test_reddit_trend_report_is_suppressed(self):
        assert classify_publish_tier(kind="csi_reddit_trend_report") == "suppressed"

    def test_trend_report_with_anomaly_is_high_value(self):
        tier = classify_publish_tier(
            kind="csi_rss_trend_report",
            metadata={"notification_policy": {"has_anomaly": True}},
        )
        assert tier == "high_value"

    def test_unknown_kind_defaults_to_digest(self):
        assert classify_publish_tier(kind="csi_something_new") == "digest"


class TestFilterNotifications:
    def test_empty_list(self):
        buckets = filter_notifications_for_publish([])
        assert buckets == {"critical": [], "high_value": [], "digest": [], "suppressed": []}

    def test_mixed_notifications(self):
        notifications = [
            {"kind": "csi_delivery_health_regression", "severity": "warning", "title": "Regression"},
            {"kind": "csi_report_product_ready", "severity": "info", "title": "Report A",
             "metadata": {"quality": {"quality_score": 0.9, "quality_grade": "A"}}},
            {"kind": "csi_delivery_health_recovered", "severity": "info", "title": "Recovered"},
            {"kind": "csi_rss_trend_report", "severity": "info", "title": "Trend"},
        ]
        buckets = filter_notifications_for_publish(notifications)
        assert len(buckets["critical"]) == 1
        assert len(buckets["high_value"]) == 1
        assert len(buckets["digest"]) == 1
        assert len(buckets["suppressed"]) == 1

    def test_publish_tier_key_added(self):
        notifications = [{"kind": "csi_reliability_slo_breach", "severity": "warning", "title": "SLO Breach"}]
        buckets = filter_notifications_for_publish(notifications)
        assert buckets["critical"][0]["publish_tier"] == "critical"

    def test_non_dict_items_skipped(self):
        notifications = [None, "not_a_dict", 42, {"kind": "csi_delivery_health_regression", "title": "Real"}]
        buckets = filter_notifications_for_publish(notifications)
        assert len(buckets["critical"]) == 1


class TestBuildExecutiveDigest:
    def test_empty_digest(self):
        digest = build_executive_digest([])
        assert "**Total events:** 0" in digest

    def test_digest_with_mixed_tiers(self):
        notifications = [
            {"kind": "csi_delivery_health_regression", "severity": "warning", "title": "Source X failing"},
            {"kind": "csi_report_product_ready", "severity": "info", "title": "AI Momentum Report",
             "metadata": {"quality": {"quality_score": 0.85, "quality_grade": "A"}}},
            {"kind": "csi_delivery_health_recovered", "severity": "info", "title": "Source X recovered"},
            {"kind": "csi_rss_trend_report", "severity": "info", "title": "Hourly trend"},
            {"kind": "csi_rss_trend_report", "severity": "info", "title": "Another trend"},
        ]
        digest = build_executive_digest(notifications)
        assert "Critical: 1" in digest
        assert "High-value: 1" in digest
        assert "Suppressed (low-value): 2" in digest
        assert "Source X failing" in digest
        assert "AI Momentum Report" in digest
        assert "[A " in digest
        assert "2 low-value notifications suppressed" in digest

    def test_digest_format_is_markdown(self):
        digest = build_executive_digest([
            {"kind": "csi_delivery_health_regression", "title": "Alert"},
        ])
        assert digest.startswith("# CSI Daily Executive Digest")

    def test_digest_suppressed_only(self):
        notifications = [
            {"kind": "csi_rss_trend_report", "severity": "info", "title": "t1"},
            {"kind": "csi_reddit_trend_report", "severity": "info", "title": "t2"},
        ]
        digest = build_executive_digest(notifications)
        assert "**Total events:** 2" in digest
        assert "Suppressed (low-value): 2" in digest
        assert "Critical Alerts" not in digest
        assert "High-Value Insights" not in digest
