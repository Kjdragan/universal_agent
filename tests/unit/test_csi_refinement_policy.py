"""Packet 18: policy table tests for CSI iterative refinement policy engine."""

import time
from datetime import datetime, timezone, timedelta

import pytest

from universal_agent.csi_refinement_policy import (
    evaluate_refinement_policy,
    RefinementDecision,
    _MIN_IMPROVEMENT_DELTA,
    _MAX_LOW_SIGNAL_STREAK_BEFORE_ESCALATE,
    _STALE_FRESHNESS_MINUTES,
    _MIN_QUALITY_FOR_CLOSE,
)


class TestCloseLoop:
    def test_confidence_met(self):
        d = evaluate_refinement_policy(
            confidence_score=0.80,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
        )
        assert d.action == "close_loop"
        assert "0.800" in d.reason
        assert d.confidence_score == 0.80

    def test_confidence_exactly_at_target(self):
        d = evaluate_refinement_policy(
            confidence_score=0.72,
            confidence_target=0.72,
            budget_remaining=1,
            budget_total=3,
        )
        assert d.action == "close_loop"

    def test_confidence_met_but_low_quality_does_not_close(self):
        d = evaluate_refinement_policy(
            confidence_score=0.80,
            confidence_target=0.72,
            quality_score=0.1,
            budget_remaining=2,
            budget_total=3,
        )
        assert d.action != "close_loop"

    def test_confidence_met_with_acceptable_quality(self):
        d = evaluate_refinement_policy(
            confidence_score=0.80,
            confidence_target=0.72,
            quality_score=0.5,
            budget_remaining=2,
            budget_total=3,
        )
        assert d.action == "close_loop"


class TestBudgetExhausted:
    def test_zero_budget(self):
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=0,
            budget_total=3,
        )
        assert d.action == "budget_exhausted"
        assert "0/3" in d.reason

    def test_negative_budget_treated_as_zero(self):
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=-1,
            budget_total=3,
        )
        assert d.action == "budget_exhausted"


class TestEscalate:
    def test_high_low_signal_streak(self):
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
            low_signal_streak=_MAX_LOW_SIGNAL_STREAK_BEFORE_ESCALATE,
        )
        assert d.action == "escalate"
        assert "Low-signal streak" in d.reason
        assert d.escalation_detail is not None

    def test_no_improvement_after_followups(self):
        d = evaluate_refinement_policy(
            confidence_score=0.55,
            confidence_target=0.72,
            budget_remaining=1,
            budget_total=3,
            previous_confidence_score=0.55,
        )
        assert d.action == "escalate"
        assert "No meaningful improvement" in d.reason

    def test_stale_data(self):
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
            freshness_minutes=_STALE_FRESHNESS_MINUTES + 1,
        )
        assert d.action == "escalate"
        assert "staleness" in d.reason.lower()


class TestSuppressed:
    def test_active_suppression(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
            suppressed_until=future,
        )
        assert d.action == "suppressed"

    def test_expired_suppression_not_suppressed(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
            suppressed_until=past,
        )
        assert d.action != "suppressed"


class TestRequestFollowup:
    def test_default_followup(self):
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=3,
            budget_total=3,
        )
        assert d.action == "request_followup"
        assert "0.500" in d.reason
        assert "3/3" in d.reason

    def test_followup_with_improving_confidence(self):
        d = evaluate_refinement_policy(
            confidence_score=0.60,
            confidence_target=0.72,
            budget_remaining=1,
            budget_total=3,
            previous_confidence_score=0.50,
        )
        assert d.action == "request_followup"
        assert d.improvement_delta == pytest.approx(0.10, abs=0.001)


class TestDecisionRecord:
    def test_to_dict(self):
        d = evaluate_refinement_policy(
            confidence_score=0.65,
            confidence_target=0.72,
            quality_score=0.7,
            quality_grade="B",
            budget_remaining=2,
            budget_total=3,
            low_signal_streak=1,
            freshness_minutes=30,
            previous_confidence_score=0.60,
        )
        record = d.to_dict()
        assert isinstance(record, dict)
        assert record["action"] == "request_followup"
        assert record["quality_score"] == pytest.approx(0.7, abs=0.01)
        assert record["quality_grade"] == "B"
        assert record["improvement_delta"] == pytest.approx(0.05, abs=0.001)

    def test_deterministic(self):
        kwargs = dict(
            confidence_score=0.55,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
        )
        d1 = evaluate_refinement_policy(**kwargs)
        d2 = evaluate_refinement_policy(**kwargs)
        assert d1.action == d2.action
        assert d1.reason == d2.reason


class TestPriorityOrder:
    """Verify that decision priorities are respected when multiple conditions are true."""

    def test_suppressed_takes_priority_over_close(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        d = evaluate_refinement_policy(
            confidence_score=0.80,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
            suppressed_until=future,
        )
        assert d.action == "suppressed"

    def test_close_takes_priority_over_budget_exhausted(self):
        d = evaluate_refinement_policy(
            confidence_score=0.80,
            confidence_target=0.72,
            budget_remaining=0,
            budget_total=3,
        )
        assert d.action == "close_loop"

    def test_budget_exhausted_takes_priority_over_escalate(self):
        d = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=0,
            budget_total=3,
            low_signal_streak=10,
        )
        assert d.action == "budget_exhausted"


class TestSimulationMixedSignals:
    """Simulate a multi-step refinement loop with mixed-source signals."""

    def test_three_step_loop(self):
        # Step 1: initial low confidence -> request followup
        d1 = evaluate_refinement_policy(
            confidence_score=0.45,
            confidence_target=0.72,
            budget_remaining=3,
            budget_total=3,
            previous_confidence_score=0.0,
        )
        assert d1.action == "request_followup"

        # Step 2: some improvement -> still request followup
        d2 = evaluate_refinement_policy(
            confidence_score=0.60,
            confidence_target=0.72,
            budget_remaining=2,
            budget_total=3,
            previous_confidence_score=0.45,
        )
        assert d2.action == "request_followup"
        assert d2.improvement_delta > 0

        # Step 3: confidence met -> close loop
        d3 = evaluate_refinement_policy(
            confidence_score=0.75,
            confidence_target=0.72,
            budget_remaining=1,
            budget_total=3,
            previous_confidence_score=0.60,
        )
        assert d3.action == "close_loop"

    def test_stalled_loop_escalates(self):
        # Step 1: initial
        d1 = evaluate_refinement_policy(
            confidence_score=0.50,
            confidence_target=0.72,
            budget_remaining=3,
            budget_total=3,
            previous_confidence_score=0.0,
        )
        assert d1.action == "request_followup"

        # Step 2: no improvement after 2 consumed
        d2 = evaluate_refinement_policy(
            confidence_score=0.51,
            confidence_target=0.72,
            budget_remaining=1,
            budget_total=3,
            previous_confidence_score=0.50,
        )
        assert d2.action == "escalate"
        assert "No meaningful improvement" in d2.reason
