"""CSI Iterative Refinement Policy Engine (Packet 18).

Combines confidence, quality, freshness, and budget into a single decision
record that determines whether to:
  - close_loop: confidence met or exceeded target
  - request_followup: confidence below target, budget remaining, quality improvable
  - escalate: stuck loop (no improvement after multiple follow-ups) or anomaly
  - budget_exhausted: no budget left, cannot follow up further
  - suppressed: low-signal streak triggered suppression cooldown

The policy is deterministic and testable with fixed inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class RefinementDecision:
    """Output of the refinement policy engine."""
    action: str  # close_loop | request_followup | escalate | budget_exhausted | suppressed
    reason: str
    confidence_score: float = 0.0
    confidence_target: float = 0.72
    quality_score: Optional[float] = None
    quality_grade: Optional[str] = None
    budget_remaining: int = 0
    budget_total: int = 3
    low_signal_streak: int = 0
    freshness_minutes: int = 0
    improvement_delta: float = 0.0
    escalation_detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Thresholds
_MIN_IMPROVEMENT_DELTA = 0.02  # minimum confidence improvement per follow-up
_MAX_LOW_SIGNAL_STREAK_BEFORE_ESCALATE = 3
_STALE_FRESHNESS_MINUTES = 360  # 6 hours
_MIN_QUALITY_FOR_CLOSE = 0.3


def evaluate_refinement_policy(
    *,
    confidence_score: float,
    confidence_target: float,
    quality_score: Optional[float] = None,
    quality_grade: Optional[str] = None,
    budget_remaining: int,
    budget_total: int,
    low_signal_streak: int = 0,
    freshness_minutes: int = 0,
    previous_confidence_score: float = 0.0,
    suppressed_until: Optional[str] = None,
) -> RefinementDecision:
    """Evaluate the refinement policy and return a decision.

    Decision priority (highest to lowest):
    1. suppressed (cooldown active)
    2. close_loop (confidence >= target AND quality acceptable)
    3. budget_exhausted (no budget left)
    4. escalate (stuck loop or anomaly)
    5. request_followup (default when improvement possible)
    """
    improvement_delta = round(confidence_score - previous_confidence_score, 4)

    base = dict(
        confidence_score=round(float(confidence_score), 4),
        confidence_target=round(float(confidence_target), 4),
        quality_score=round(float(quality_score), 4) if quality_score is not None else None,
        quality_grade=quality_grade,
        budget_remaining=int(budget_remaining),
        budget_total=int(budget_total),
        low_signal_streak=int(low_signal_streak),
        freshness_minutes=int(freshness_minutes),
        improvement_delta=improvement_delta,
    )

    # 1. Suppressed (cooldown active)
    if suppressed_until:
        import time
        from datetime import datetime, timezone
        try:
            suppressed_dt = datetime.fromisoformat(suppressed_until.replace("Z", "+00:00"))
            if suppressed_dt.replace(tzinfo=timezone.utc if suppressed_dt.tzinfo is None else suppressed_dt.tzinfo).timestamp() > time.time():
                return RefinementDecision(
                    action="suppressed",
                    reason=f"Low-signal suppression active until {suppressed_until}",
                    **base,
                )
        except Exception:
            pass

    # 2. Close loop (confidence met + quality acceptable)
    quality_ok = quality_score is None or float(quality_score or 0) >= _MIN_QUALITY_FOR_CLOSE
    if float(confidence_score) >= float(confidence_target) and quality_ok:
        return RefinementDecision(
            action="close_loop",
            reason=f"Confidence {confidence_score:.3f} >= target {confidence_target:.3f}",
            **base,
        )

    # 3. Budget exhausted
    if int(budget_remaining) <= 0:
        return RefinementDecision(
            action="budget_exhausted",
            reason=f"Follow-up budget exhausted (0/{budget_total})",
            **base,
        )

    # 4. Escalate conditions
    # 4a. High low-signal streak
    if int(low_signal_streak) >= _MAX_LOW_SIGNAL_STREAK_BEFORE_ESCALATE:
        return RefinementDecision(
            action="escalate",
            reason=f"Low-signal streak {low_signal_streak} >= threshold {_MAX_LOW_SIGNAL_STREAK_BEFORE_ESCALATE}",
            escalation_detail="Consider expanding source coverage or adjusting confidence target.",
            **base,
        )

    # 4b. No improvement after follow-ups (budget partially consumed)
    budget_consumed = int(budget_total) - int(budget_remaining)
    if budget_consumed >= 2 and improvement_delta < _MIN_IMPROVEMENT_DELTA:
        return RefinementDecision(
            action="escalate",
            reason=f"No meaningful improvement after {budget_consumed} follow-ups (delta={improvement_delta:.4f})",
            escalation_detail="Loop appears stuck. Manual review or source expansion recommended.",
            **base,
        )

    # 4c. Stale data
    if int(freshness_minutes) > _STALE_FRESHNESS_MINUTES:
        return RefinementDecision(
            action="escalate",
            reason=f"Data staleness {freshness_minutes}min exceeds threshold {_STALE_FRESHNESS_MINUTES}min",
            escalation_detail="Source data may be outdated. Check adapter health and source availability.",
            **base,
        )

    # 5. Request follow-up (default)
    return RefinementDecision(
        action="request_followup",
        reason=f"Confidence {confidence_score:.3f} < target {confidence_target:.3f}, budget {budget_remaining}/{budget_total}",
        **base,
    )
