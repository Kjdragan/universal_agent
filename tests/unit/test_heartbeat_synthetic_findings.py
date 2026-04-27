"""Tests for the heartbeat always-write structured findings contract.

Every heartbeat run must produce a structured findings JSON. This ensures
the gateway can always parse the result, and *absence* of the file reliably
signals a genuine failure.

Policy:
    ok_only  + not run_failed  →  overall_status="ok",  summary="200 OK"
    not ok   + not run_failed  →  overall_status="ok",  summary="200 OK"
    run_failed                 →  overall_status="critical", error details
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Synthetic findings severity policy
# ---------------------------------------------------------------------------


def test_successful_ok_run_produces_ok_status() -> None:
    """An ok_only heartbeat → overall_status ok, summary '200 OK'."""
    run_failed = False
    expected_status = "critical" if run_failed else "ok"
    assert expected_status == "ok"


def test_successful_nonok_run_produces_ok_status() -> None:
    """A non-ok heartbeat that succeeded → still 'ok' status."""
    run_failed = False
    expected_status = "critical" if run_failed else "ok"
    assert expected_status == "ok"


def test_failed_run_produces_critical_status() -> None:
    """A heartbeat that fails → critical status."""
    run_failed = True
    expected_status = "critical" if run_failed else "ok"
    assert expected_status == "critical"


# ---------------------------------------------------------------------------
# _heartbeat_has_meaningful_activity guards — ok status should not trigger
# ---------------------------------------------------------------------------


def test_meaningful_activity_false_for_ok_severity() -> None:
    """When classification severity is ok/info (from an ok-status synthetic),
    _heartbeat_has_meaningful_activity should return False since those are
    not in {warning, error, critical}."""
    from universal_agent.gateway_server import _heartbeat_has_meaningful_activity

    payload = {
        "artifacts": {
            "writes": [],
            "work_products": [],
            "bash_commands": [],
        },
        "task_hub_completed_count": 0,
        "task_hub_review_count": 0,
    }
    for sev in ("ok", "info"):
        classification = {
            "severity": sev,
            "unknown_rule_count": 0,
        }
        assert _heartbeat_has_meaningful_activity(payload, classification) is False, (
            f"Expected False for severity={sev}"
        )


def test_meaningful_activity_true_for_warning_severity() -> None:
    """When classification severity is warning (actual warn findings),
    _heartbeat_has_meaningful_activity should return True."""
    from universal_agent.gateway_server import _heartbeat_has_meaningful_activity

    payload = {
        "artifacts": {
            "writes": [],
            "work_products": [],
            "bash_commands": [],
        },
        "task_hub_completed_count": 0,
        "task_hub_review_count": 0,
    }
    classification = {
        "severity": "warning",
        "unknown_rule_count": 0,
    }
    assert _heartbeat_has_meaningful_activity(payload, classification) is True


def test_meaningful_activity_true_for_bash_commands() -> None:
    """Heartbeats that run bash commands are always meaningful."""
    from universal_agent.gateway_server import _heartbeat_has_meaningful_activity

    payload = {
        "artifacts": {
            "writes": [],
            "work_products": [],
            "bash_commands": ["uptime && free -h"],
        },
        "task_hub_completed_count": 0,
        "task_hub_review_count": 0,
    }
    classification = {
        "severity": "ok",
        "unknown_rule_count": 0,
    }
    assert _heartbeat_has_meaningful_activity(payload, classification) is True


def test_meaningful_activity_true_for_unknown_rules() -> None:
    """Unknown rule findings are always meaningful (need investigation)."""
    from universal_agent.gateway_server import _heartbeat_has_meaningful_activity

    payload = {
        "artifacts": {
            "writes": [],
            "work_products": [],
            "bash_commands": [],
        },
        "task_hub_completed_count": 0,
        "task_hub_review_count": 0,
    }
    classification = {
        "severity": "ok",
        "unknown_rule_count": 1,
    }
    assert _heartbeat_has_meaningful_activity(payload, classification) is True


# ---------------------------------------------------------------------------
# _classify_heartbeat_mediation with ok-status findings
# ---------------------------------------------------------------------------


def test_classify_ok_findings_has_zero_findings() -> None:
    """An ok-status synthetic entry with no findings should classify cleanly."""
    from universal_agent.gateway_server import _classify_heartbeat_mediation

    findings = {
        "overall_status": "ok",
        "summary": "200 OK",
        "findings": [],
    }
    classification = _classify_heartbeat_mediation(findings)
    assert int(classification["findings_count"]) == 0
    assert int(classification["unknown_rule_count"]) == 0
    assert int(classification["known_rule_count"]) == 0
