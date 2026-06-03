"""Regression tests for the heartbeat investigation anti-collapse gate.

Background: on 2026-06-03 a heartbeat finding set contained a false-positive
CSI freshness alert (a real code bug, now fixed) AND a genuine true-positive
``csi_source_liveness`` finding (5 of 8 CSI adapters silently dark for weeks).
The investigation collapsed the whole set into a single
``false_positive_with_code_bug`` verdict with operator_review_required=false,
masking the real outage. ``derive_heartbeat_operator_review`` is the
deterministic backstop that prevents one false positive from hiding another
finding's real/critical problem.
"""

from universal_agent.heartbeat_mediation import derive_heartbeat_operator_review


def test_real_finding_forces_review_despite_global_false_positive():
    """The exact masking scenario: global verdict says no-review, but a
    per-finding entry is ``real`` -> operator review is forced on."""
    payload = {
        "classification": "false_positive_with_code_bug",
        "operator_review_required": False,
        "findings_triage": [
            {
                "finding_id": "csi_rss_all_channels_stale",
                "severity": "critical",
                "verdict": "false_positive",
            },
            {
                "finding_id": "csi_source_liveness",
                "severity": "critical",
                "verdict": "real",
            },
        ],
    }
    review, triage = derive_heartbeat_operator_review(payload)
    assert review is True
    assert len(triage) == 2


def test_critical_finding_not_cleared_forces_review():
    """A critical finding that is only marked 'monitor' (not positively cleared
    as a false positive) must still reach the operator."""
    payload = {
        "operator_review_required": False,
        "findings_triage": [
            {"finding_id": "x", "severity": "critical", "verdict": "monitor"},
        ],
    }
    review, _ = derive_heartbeat_operator_review(payload)
    assert review is True


def test_all_false_positive_respects_global_flag():
    """When every finding is positively cleared as a false positive, the gate
    does not invent a review requirement."""
    payload = {
        "operator_review_required": False,
        "findings_triage": [
            {"finding_id": "a", "severity": "critical", "verdict": "false_positive"},
            {"finding_id": "b", "severity": "warn", "verdict": "false_positive"},
        ],
    }
    review, _ = derive_heartbeat_operator_review(payload)
    assert review is False


def test_per_finding_review_flag_propagates():
    payload = {
        "operator_review_required": False,
        "findings_triage": [
            {
                "finding_id": "a",
                "severity": "warn",
                "verdict": "monitor",
                "operator_review_required": True,
            },
        ],
    }
    review, _ = derive_heartbeat_operator_review(payload)
    assert review is True


def test_no_triage_preserves_explicit_true():
    """Backward-compat: no findings_triage -> honour the explicit global flag."""
    review, triage = derive_heartbeat_operator_review(
        {"operator_review_required": True}
    )
    assert review is True
    assert triage == []


def test_no_triage_preserves_explicit_false():
    review, triage = derive_heartbeat_operator_review(
        {"operator_review_required": False}
    )
    assert review is False
    assert triage == []


def test_malformed_triage_is_ignored_safely():
    payload = {
        "operator_review_required": False,
        "findings_triage": ["not-a-dict", 42, None],
    }
    review, triage = derive_heartbeat_operator_review(payload)
    assert review is False
    assert triage == []
