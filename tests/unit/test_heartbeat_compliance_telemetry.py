"""Unit tests for the non-blocking heartbeat compliance-telemetry helpers.

These helpers (`assess_triage_compliance`, `assess_proactive_health_inclusion`)
only *observe* whether the heartbeat investigation LLM followed two
``HEARTBEAT.md`` instructions — they never gate behavior. The tests pin the
observation logic so the telemetry stays meaningful.
"""
from __future__ import annotations

from universal_agent.heartbeat_mediation import (
    assess_proactive_health_inclusion,
    assess_triage_compliance,
)


# --------------------------------------------------------------------------- #
# assess_triage_compliance
# --------------------------------------------------------------------------- #
def test_triage_compliance_missing_array_is_non_compliant():
    compliant, reason = assess_triage_compliance({})
    assert compliant is False
    assert reason == "missing_findings_triage"


def test_triage_compliance_empty_array_is_non_compliant():
    compliant, reason = assess_triage_compliance({"findings_triage": []})
    assert compliant is False
    assert reason == "missing_findings_triage"


def test_triage_compliance_non_list_is_non_compliant():
    compliant, reason = assess_triage_compliance({"findings_triage": "real"})
    assert compliant is False
    assert reason == "missing_findings_triage"


def test_triage_compliance_entry_missing_verdict_is_flagged():
    payload = {
        "findings_triage": [
            {"finding_id": "a", "verdict": "real"},
            {"finding_id": "b"},  # no verdict
        ]
    }
    compliant, reason = assess_triage_compliance(payload)
    assert compliant is False
    assert reason == "triage_entries_missing_verdict=1/2"


def test_triage_compliance_full_per_finding_triage_is_ok():
    payload = {
        "findings_triage": [
            {"finding_id": "a", "verdict": "real"},
            {"finding_id": "b", "verdict": "false_positive"},
        ]
    }
    compliant, reason = assess_triage_compliance(payload)
    assert compliant is True
    assert reason == "ok"


def test_triage_compliance_ignores_non_dict_entries_then_flags_empty():
    # A list of junk entries collapses to zero usable triage rows.
    compliant, reason = assess_triage_compliance({"findings_triage": ["x", 3, None]})
    assert compliant is False
    assert reason == "missing_findings_triage"


# --------------------------------------------------------------------------- #
# assess_proactive_health_inclusion
# --------------------------------------------------------------------------- #
def _snapshot(invariants):
    return {"payload": {"invariants": invariants}}


def test_inclusion_no_snapshot_findings_reports_no_gap():
    included, reason = assess_proactive_health_inclusion(_snapshot([]), [])
    assert included is True
    assert reason == "no_snapshot_findings"


def test_inclusion_none_snapshot_reports_no_gap():
    included, reason = assess_proactive_health_inclusion(None, [])
    assert included is True
    assert reason == "no_snapshot_findings"


def test_inclusion_only_ok_severity_reports_no_gap():
    snap = _snapshot([{"severity": "ok", "metric_key": "x"}])
    included, reason = assess_proactive_health_inclusion(snap, [])
    assert included is True
    assert reason == "no_snapshot_findings"


def test_inclusion_critical_snapshot_absent_from_artifact_is_gap():
    snap = _snapshot([{"severity": "critical", "metric_key": "youtube_transcript_coverage"}])
    artifact_findings = [{"category": "database_health", "title": "db ok"}]
    included, reason = assess_proactive_health_inclusion(snap, artifact_findings)
    assert included is False
    assert reason == "snapshot_had_1_findings_absent_from_artifact"


def test_inclusion_critical_snapshot_present_in_artifact_is_ok():
    snap = _snapshot(
        [
            {"severity": "critical", "metric_key": "youtube_transcript_coverage"},
            {"severity": "warn", "metric_key": "cron_staleness"},
        ]
    )
    artifact_findings = [
        {"category": "proactive_health", "title": "YouTube transcript coverage"},
    ]
    included, reason = assess_proactive_health_inclusion(snap, artifact_findings)
    assert included is True
    assert reason == "ok"


def test_inclusion_warn_only_snapshot_absent_is_gap():
    snap = _snapshot([{"severity": "warn", "metric_key": "cron_staleness"}])
    included, reason = assess_proactive_health_inclusion(snap, [])
    assert included is False
    assert reason == "snapshot_had_1_findings_absent_from_artifact"


def test_inclusion_tolerates_non_dict_artifact_findings():
    snap = _snapshot([{"severity": "critical", "metric_key": "x"}])
    included, reason = assess_proactive_health_inclusion(snap, ["junk", None, 5])
    assert included is False
    assert reason == "snapshot_had_1_findings_absent_from_artifact"
