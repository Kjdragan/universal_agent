"""CSI High-Signal Publishing Pipeline (Packet 19).

Filters CSI notifications into publish tiers to suppress repetitive
low-value status messages and surface only meaningful outputs.

Tiers:
  - critical: delivery regressions, SLO breaches, auto-remediation failures
  - high_value: high-confidence opportunities, grade-A quality reports
  - digest: daily executive summary (batched)
  - suppressed: low-value repetitive status updates

Each notification is classified into exactly one tier.
"""

from __future__ import annotations

from typing import Any, Optional


# Notification kinds that are always critical
_CRITICAL_KINDS = {
    "csi_delivery_health_regression",
    "csi_reliability_slo_breach",
    "csi_delivery_health_auto_remediation_failed",
}

# Notification kinds eligible for high-value tier
_HIGH_VALUE_CANDIDATE_KINDS = {
    "csi_report_product_ready",
    "csi_opportunity_bundle_ready",
    "csi_rss_insight_daily",
    "csi_rss_insight_emerging",
}

# Notification kinds that are recovery/operational status (digestible)
_DIGEST_KINDS = {
    "csi_delivery_health_recovered",
    "csi_reliability_slo_recovery",
    "csi_delivery_health_auto_remediation_succeeded",
    "csi_specialist_hourly_synthesis",
}

# Kinds that are low-value repetitive noise
_SUPPRESSED_KINDS = {
    "csi_rss_trend_report",
    "csi_reddit_trend_report",
}

# Quality thresholds for high-value tier
_HIGH_VALUE_QUALITY_THRESHOLD = 0.6
_HIGH_VALUE_CONFIDENCE_THRESHOLD = 0.7


def classify_publish_tier(
    *,
    kind: str,
    severity: str = "info",
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Classify a CSI notification into a publish tier.

    Returns one of: 'critical', 'high_value', 'digest', 'suppressed'.
    """
    kind_norm = str(kind or "").strip().lower()
    severity_norm = str(severity or "info").strip().lower()
    meta = metadata if isinstance(metadata, dict) else {}

    # Critical: always publish immediately
    if kind_norm in _CRITICAL_KINDS:
        return "critical"
    if severity_norm in ("critical", "error"):
        return "critical"

    # High-value candidates: check quality/confidence gates
    if kind_norm in _HIGH_VALUE_CANDIDATE_KINDS:
        quality = meta.get("quality")
        if isinstance(quality, dict):
            score = float(quality.get("quality_score") or 0)
            grade = str(quality.get("quality_grade") or "")
            if score >= _HIGH_VALUE_QUALITY_THRESHOLD or grade in ("A", "B"):
                return "high_value"

        # Check notification policy for anomaly/high-value flags
        policy = meta.get("notification_policy")
        if isinstance(policy, dict):
            if policy.get("high_value") or policy.get("has_anomaly"):
                return "high_value"

        # Fall through to digest if quality is low
        return "digest"

    # Digest: operational status updates
    if kind_norm in _DIGEST_KINDS:
        return "digest"

    # Suppressed: low-value repetitive noise
    if kind_norm in _SUPPRESSED_KINDS:
        # Unless it has an anomaly flag
        policy = meta.get("notification_policy")
        if isinstance(policy, dict) and policy.get("has_anomaly"):
            return "high_value"
        return "suppressed"

    # Default: digest (unknown kinds get batched, not published immediately)
    return "digest"


def filter_notifications_for_publish(
    notifications: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Classify a list of notifications into publish tier buckets.

    Returns dict with keys: critical, high_value, digest, suppressed.
    Each value is a list of notification dicts with an added 'publish_tier' key.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        "critical": [],
        "high_value": [],
        "digest": [],
        "suppressed": [],
    }
    for notif in notifications:
        if not isinstance(notif, dict):
            continue
        tier = classify_publish_tier(
            kind=str(notif.get("kind") or ""),
            severity=str(notif.get("severity") or "info"),
            metadata=notif.get("metadata"),
        )
        enriched = {**notif, "publish_tier": tier}
        buckets[tier].append(enriched)
    return buckets


def build_executive_digest(
    notifications: list[dict[str, Any]],
) -> str:
    """Build a daily executive digest from classified notifications.

    Returns a formatted markdown string summarizing the day's CSI activity.
    """
    buckets = filter_notifications_for_publish(notifications)
    critical_count = len(buckets["critical"])
    high_value_count = len(buckets["high_value"])
    digest_count = len(buckets["digest"])
    suppressed_count = len(buckets["suppressed"])
    total = critical_count + high_value_count + digest_count + suppressed_count

    lines = [
        "# CSI Daily Executive Digest",
        "",
        f"**Total events:** {total}",
        f"- Critical: {critical_count}",
        f"- High-value: {high_value_count}",
        f"- Digest (operational): {digest_count}",
        f"- Suppressed (low-value): {suppressed_count}",
        "",
    ]

    if critical_count > 0:
        lines.append("## Critical Alerts")
        for n in buckets["critical"][:10]:
            lines.append(f"- **{n.get('title', 'Untitled')}** ({n.get('kind', '')})")
        lines.append("")

    if high_value_count > 0:
        lines.append("## High-Value Insights")
        for n in buckets["high_value"][:10]:
            quality = (n.get("metadata") or {}).get("quality") or {}
            grade = quality.get("quality_grade", "")
            score_pct = f"{float(quality.get('quality_score', 0)) * 100:.0f}%" if quality.get("quality_score") else ""
            badge = f" [{grade} {score_pct}]" if grade else ""
            lines.append(f"- **{n.get('title', 'Untitled')}**{badge}")
        lines.append("")

    if suppressed_count > 0:
        lines.append(f"*{suppressed_count} low-value notifications suppressed from feed.*")
        lines.append("")

    return "\n".join(lines)
