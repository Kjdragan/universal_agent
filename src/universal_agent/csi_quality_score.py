"""CSI report quality scoring v1 (Packet 16).

Computes a per-report quality score from four dimensions:
  - evidence_coverage: fraction of expected evidence fields present
  - novelty: whether report covers new topics vs. rehash
  - source_diversity: number of distinct sources contributing
  - actionability: whether report contains actionable recommendations

Each dimension is 0.0-1.0; the composite score is a weighted average.
"""

from __future__ import annotations

from typing import Any


# Weights for composite score (must sum to 1.0)
_WEIGHTS = {
    "evidence_coverage": 0.30,
    "novelty": 0.20,
    "source_diversity": 0.25,
    "actionability": 0.25,
}

# Evidence fields we expect on a well-formed report subject
_EXPECTED_EVIDENCE_FIELDS = [
    "report_key",
    "window_start_utc",
    "window_end_utc",
    "artifact_paths",
    "quality_summary",
]

# Source diversity thresholds
_MIN_SOURCES_FOR_FULL_SCORE = 3


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _evidence_coverage_score(subject: dict[str, Any]) -> float:
    """Score based on how many expected evidence fields are present and non-empty."""
    if not subject:
        return 0.0
    present = 0
    for field in _EXPECTED_EVIDENCE_FIELDS:
        val = subject.get(field)
        if val is not None and val != "" and val != {}:
            present += 1
    return round(present / len(_EXPECTED_EVIDENCE_FIELDS), 3)


def _novelty_score(subject: dict[str, Any], *, prior_report_keys: list[str] | None = None) -> float:
    """Score based on whether this report covers new ground.

    If prior_report_keys is provided, checks if current report_key is novel.
    Also boosts for high total_items (more data = more likely novel insights).
    """
    base = 0.5
    report_key = str(subject.get("report_key") or "").strip()
    if prior_report_keys is not None and report_key:
        if report_key not in prior_report_keys:
            base = 0.9
        else:
            base = 0.3

    # Volume boost: more items = richer potential for novel insights
    quality = subject.get("quality_summary") if isinstance(subject.get("quality_summary"), dict) else {}
    totals = subject.get("totals") if isinstance(subject.get("totals"), dict) else {}
    total_items = _to_int(
        quality.get("signal_volume") or subject.get("total_items") or totals.get("items"),
        default=0,
    )
    volume_boost = min(0.2, (min(total_items, 20) / 20.0) * 0.2)
    return round(_clamp(base + volume_boost), 3)


def _source_diversity_score(source_mix: dict[str, int]) -> float:
    """Score based on number of distinct non-zero sources."""
    active_sources = len([k for k, v in source_mix.items() if _to_int(v) > 0])
    if active_sources <= 0:
        return 0.0
    if active_sources >= _MIN_SOURCES_FOR_FULL_SCORE:
        return 1.0
    return round(active_sources / _MIN_SOURCES_FOR_FULL_SCORE, 3)


def _actionability_score(subject: dict[str, Any]) -> float:
    """Score based on presence of actionable content indicators."""
    score = 0.0

    # Has artifact paths (operator can read the report)
    artifact_paths = subject.get("artifact_paths")
    if isinstance(artifact_paths, dict) and (artifact_paths.get("markdown") or artifact_paths.get("json")):
        score += 0.4

    # Has opportunities (concrete action items)
    opportunities = subject.get("opportunities")
    if isinstance(opportunities, list) and len(opportunities) > 0:
        score += 0.3
        # Bonus for multiple opportunities
        if len(opportunities) >= 3:
            score += 0.1

    # Has quality_summary (structured quality data)
    if isinstance(subject.get("quality_summary"), dict):
        score += 0.2

    return round(_clamp(score), 3)


def score_report_quality(
    *,
    subject: dict[str, Any],
    source_mix: dict[str, int] | None = None,
    prior_report_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Compute composite quality score for a CSI report.

    Returns dict with:
      - quality_score: float 0.0-1.0
      - quality_grade: str (A/B/C/D)
      - dimensions: dict of individual dimension scores
    """
    subject_obj = subject if isinstance(subject, dict) else {}
    source_mix_obj = source_mix if isinstance(source_mix, dict) else {}

    evidence = _evidence_coverage_score(subject_obj)
    novelty = _novelty_score(subject_obj, prior_report_keys=prior_report_keys)
    diversity = _source_diversity_score(source_mix_obj)
    actionability = _actionability_score(subject_obj)

    composite = (
        _WEIGHTS["evidence_coverage"] * evidence
        + _WEIGHTS["novelty"] * novelty
        + _WEIGHTS["source_diversity"] * diversity
        + _WEIGHTS["actionability"] * actionability
    )
    composite = round(_clamp(composite), 3)

    if composite >= 0.8:
        grade = "A"
    elif composite >= 0.6:
        grade = "B"
    elif composite >= 0.4:
        grade = "C"
    else:
        grade = "D"

    return {
        "quality_score": composite,
        "quality_grade": grade,
        "dimensions": {
            "evidence_coverage": evidence,
            "novelty": novelty,
            "source_diversity": diversity,
            "actionability": actionability,
        },
    }
