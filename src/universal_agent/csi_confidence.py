"""CSI specialist confidence scoring helpers.

Provides an evidence-aware scorer with deterministic fallback to the legacy
heuristic method when structured evidence is sparse.
"""

from __future__ import annotations

from typing import Any


def confidence_baseline(event_type: str) -> float:
    lowered = str(event_type or "").strip().lower()
    if lowered == "opportunity_bundle_ready":
        return 0.88
    if lowered == "report_product_ready":
        return 0.82
    if lowered == "rss_insight_daily":
        return 0.76
    if lowered in {"rss_trend_report", "reddit_trend_report"}:
        return 0.64
    if lowered == "rss_insight_emerging":
        return 0.58
    return 0.6


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _signal_volume(subject: dict[str, Any]) -> int:
    quality = subject.get("quality_summary") if isinstance(subject.get("quality_summary"), dict) else {}
    totals = subject.get("totals") if isinstance(subject.get("totals"), dict) else {}
    candidates = [
        quality.get("signal_volume"),
        subject.get("total_items"),
        totals.get("items"),
    ]
    for raw in candidates:
        if raw is not None:
            value = _to_int(raw, default=0)
            if value >= 0:
                return value
    return 0


def _freshness_minutes(subject: dict[str, Any]) -> int:
    quality = subject.get("quality_summary") if isinstance(subject.get("quality_summary"), dict) else {}
    raw = quality.get("freshness_minutes")
    if raw is None:
        return 0
    return max(0, _to_int(raw, default=0))


def _opportunity_count(subject: dict[str, Any]) -> int:
    opportunities = subject.get("opportunities")
    if not isinstance(opportunities, list):
        return 0
    return len([item for item in opportunities if isinstance(item, dict)])


def heuristic_confidence(*, event_type: str, events_count: int, source_mix: dict[str, int]) -> float:
    baseline = confidence_baseline(event_type)
    diversity = len([k for k, v in source_mix.items() if int(v or 0) > 0])
    diversity_bonus = min(0.18, max(0, diversity - 1) * 0.07)
    volume_bonus = min(0.1, max(0, int(events_count) - 1) * 0.02)
    return round(min(0.95, baseline + diversity_bonus + volume_bonus), 3)


def score_event_confidence(
    *,
    event_type: str,
    subject: dict[str, Any],
    events_count: int,
    source_mix: dict[str, int],
) -> dict[str, Any]:
    """Return confidence score + method + evidence summary for CSI loops."""
    subject_obj = subject if isinstance(subject, dict) else {}
    source_diversity = len([k for k, v in source_mix.items() if int(v or 0) > 0])
    signal_volume = _signal_volume(subject_obj)
    freshness_minutes = _freshness_minutes(subject_obj)
    opportunity_count = _opportunity_count(subject_obj)
    has_quality_summary = isinstance(subject_obj.get("quality_summary"), dict)

    heuristic_score = heuristic_confidence(
        event_type=event_type,
        events_count=events_count,
        source_mix=source_mix,
    )
    has_structured_evidence = bool(
        has_quality_summary
        or opportunity_count > 0
        or signal_volume >= 8
    )
    if not has_structured_evidence:
        return {
            "score": heuristic_score,
            "method": "heuristic",
            "evidence": {
                "signal_volume": signal_volume,
                "source_diversity": source_diversity,
                "opportunity_count": opportunity_count,
                "freshness_minutes": freshness_minutes,
                "events_count": int(events_count),
            },
        }

    baseline = confidence_baseline(event_type)
    diversity_bonus = min(0.16, max(0, source_diversity - 1) * 0.06)
    signal_bonus = min(0.12, (min(signal_volume, 30) / 30.0) * 0.12)
    opportunity_bonus = min(0.12, (min(opportunity_count, 6) / 6.0) * 0.12)
    events_bonus = min(0.08, max(0, int(events_count) - 1) * 0.015)
    freshness_penalty = 0.0
    if freshness_minutes > 120:
        freshness_penalty = min(0.1, ((freshness_minutes - 120) / 360.0) * 0.1)
    quality_bonus = 0.04 if has_quality_summary else 0.0

    evidence_score = baseline + diversity_bonus + signal_bonus + opportunity_bonus + events_bonus + quality_bonus - freshness_penalty
    score = round(_clamp(evidence_score, 0.2, 0.95), 3)
    # Never allow evidence model to regress far below heuristic fallback.
    score = round(max(score, heuristic_score - 0.03), 3)
    return {
        "score": score,
        "method": "evidence_model",
        "evidence": {
            "signal_volume": signal_volume,
            "source_diversity": source_diversity,
            "opportunity_count": opportunity_count,
            "freshness_minutes": freshness_minutes,
            "events_count": int(events_count),
            "has_quality_summary": bool(has_quality_summary),
            "heuristic_score": heuristic_score,
        },
    }
