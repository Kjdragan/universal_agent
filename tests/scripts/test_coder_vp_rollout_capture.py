from scripts.coder_vp_rollout_capture import _assess, _assess_with_profile, _format_row


def test_assess_holds_when_no_traffic():
    snapshot = {
        "fallback": {"missions_considered": 0, "missions_with_fallback": 0, "rate": 0.0},
        "latency_seconds": {"p95_seconds": None},
    }
    result = _assess(snapshot)
    assert result.decision == "HOLD_SHADOW"
    assert any("no mission traffic" in reason for reason in result.reasons)


def test_assess_holds_when_fallback_rate_elevated():
    snapshot = {
        "fallback": {"missions_considered": 10, "missions_with_fallback": 1, "rate": 0.1},
        "latency_seconds": {"p95_seconds": 0.4},
    }
    result = _assess(snapshot)
    assert result.decision == "HOLD_SHADOW"
    assert any("fallback rate elevated" in reason for reason in result.reasons)


def test_assess_ready_when_healthy_window():
    snapshot = {
        "fallback": {"missions_considered": 8, "missions_with_fallback": 0, "rate": 0.0},
        "latency_seconds": {"p95_seconds": 0.37},
    }
    result = _assess(snapshot)
    assert result.decision == "READY_FOR_LIMITED_COHORT_PILOT"


def test_format_row_contains_decision_and_metrics():
    snapshot = {
        "generated_at": "2026-02-16T14:07:42+00:00",
        "fallback": {"missions_considered": 4, "missions_with_fallback": 0, "rate": 0.0},
        "latency_seconds": {"p95_seconds": 0.377},
        "mission_counts": {"completed": 4},
        "event_counts": {"vp.mission.dispatched": 4, "vp.mission.completed": 4},
    }
    assessment = _assess(snapshot)
    row = _format_row(
        snapshot=snapshot,
        window_label="Shadow window #3",
        scope="vp.coder.primary clean simulation",
        ref='_vp_metrics_snapshot(vp_id="vp.coder.primary", mission_limit=100, event_limit=500)',
        assessment=assessment,
    )
    assert "READY_FOR_LIMITED_COHORT_PILOT" in row
    assert "| 0.000 | 0.377s |" in row


def test_sustained_profile_reports_default_on_when_healthy():
    snapshot = {
        "fallback": {"missions_considered": 18, "missions_with_fallback": 0, "rate": 0.0},
        "latency_seconds": {"p95_seconds": 35.964},
    }
    result = _assess_with_profile(snapshot, assessment_profile="sustained")
    assert result.decision == "SUSTAINED_DEFAULT_ON_HEALTHY"


def test_sustained_profile_forces_fallback_when_latency_critical():
    snapshot = {
        "fallback": {"missions_considered": 20, "missions_with_fallback": 0, "rate": 0.0},
        "latency_seconds": {"p95_seconds": 120.0},
    }
    result = _assess_with_profile(
        snapshot,
        assessment_profile="sustained",
        max_p95_critical_seconds=90.0,
    )
    assert result.decision == "SUSTAINED_FORCE_FALLBACK"
