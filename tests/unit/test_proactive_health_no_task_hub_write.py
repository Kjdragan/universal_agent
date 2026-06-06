"""Regression guard: proactive_health Task Hub plumbing stays deleted.

As of the 2026-06-03 surface change, proactive_health invariant findings are
delivered via critical email (the durable alert-of-record) + the live
GET /api/v1/ops/proactive_health endpoint. The notifier writes ZERO rows into
``task_hub_items`` — the Task Hub emit + warn-escalation helpers were removed.

The behavioral coverage that exercised this through the in-process pre-flight
(``run_pre_flight_check``) was retired in S5 Phase C along with that function
(the compute moved to the deploy-independent systemd timer). What remains is the
structural guard below: a future regression that re-adds the Task Hub emit
plumbing must fail loudly.
"""

from __future__ import annotations

from universal_agent.services import proactive_health_notifier as notifier


def test_dead_task_hub_helpers_are_removed():
    """The Task Hub emit + warn-escalation plumbing is deleted from the module."""
    for symbol in (
        "_emit_to_task_hub",
        "_track_and_filter_warns_for_escalation",
        "_warn_threshold",
        "_warn_invariants",
        "WARN_ESCALATION_THRESHOLD",
        "_consecutive_warns",
    ):
        assert not hasattr(notifier, symbol), f"{symbol} should have been deleted"
    # The critical-email path helper must survive.
    assert hasattr(notifier, "_critical_invariants")
