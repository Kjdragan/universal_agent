"""Pure-function tests for the ZAI activity health resolver.

The contract these lock (the corrected design from adversarial review):
  * GREEN comes ONLY from a real systemd last-run success — never from the absence
    of an invariant finding (which would false-green during a probe's off-window).
  * A mapped invariant finding only ESCALATES a row (degraded/critical), gated on
    its sub-target for shared invariants (reports trio / csi_source_liveness).
  * `deep_probe=False` surfaces running-but-unverified units (the blind spot).
"""

from __future__ import annotations

from universal_agent.services.zai_activity_health import (
    inprocess_health,
    resolve_activity_health,
)


def _timer(unit: str) -> dict:
    return {"unit": unit, "group": "timers", "label": unit}


def _service(unit: str, *, active_state="active", sub_state="running", n_restarts="0") -> dict:
    return {
        "unit": unit, "group": "services", "label": unit,
        "active_state": active_state, "sub_state": sub_state, "n_restarts": n_restarts,
    }


def _sibling_ok(unit: str) -> dict:
    """sibling .service props for a healthy oneshot timer (ran this boot, exited 0).
    Monotonic MUST be non-zero — that is the only reliable ran-this-boot signal."""
    return {unit[:-6] + ".service": {
        "Result": "success", "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "12345",
    }}


def _sibling_never_ran(unit: str) -> dict:
    """The false-green trap: a LOADED-but-never-run oneshot. systemd defaults
    Result=success/ExecMainStatus=0, but ExecMainStartTimestampMonotonic=0."""
    return {unit[:-6] + ".service": {
        "Result": "success", "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "0",
    }}


def _payload(*findings: dict) -> dict:
    return {"invariants": list(findings)}


def _finding(metric_key: str, severity="warn", observed_value=None, recommendation="problem") -> dict:
    return {
        "finding_id": f"invariant:{metric_key}",
        "metric_key": metric_key,
        "severity": severity,
        "observed_value": observed_value,
        "recommendation": recommendation,
    }


CONV = "universal-agent-csi-convergence-sync.timer"
SKILL = "universal-agent-skill-gap-finder.timer"
GOLD = "universal-agent-youtube-gold-channel-poller.timer"
PLAYLIST = "universal-agent-youtube-playlist-poller.timer"
MORNING = "universal-agent-morning-briefing.timer"
R_MORNING = "universal-agent-proactive-report-morning.timer"
R_MIDDAY = "universal-agent-proactive-report-midday.timer"
R_AFTERNOON = "universal-agent-proactive-report-afternoon.timer"
DISCORD = "ua-discord-intelligence.service"


def test_invariant_fired_escalates_over_systemd_green():
    """Deep invariant fires (warn) → degraded/invariant even though the sibling
    service exited 0. This is the csi-quality-assessment class: exit-0 but broken."""
    acts = [_timer(CONV)]
    payload = _payload(_finding("csi_convergence_sync_freshness", "warn"))
    out = resolve_activity_health(acts, payload, _sibling_ok(CONV))
    h = out[CONV]
    assert h["status"] == "degraded"
    assert h["source"] == "invariant"
    assert h["deep_probe"] is True
    assert h["finding_id"] == "csi_convergence_sync_freshness"


def test_invariant_clean_plus_systemd_success_is_verified_green():
    """Covered by an invariant, none fired, last run exited 0 → healthy via systemd
    (NOT via absence-of-finding). deep_probe True == verified."""
    out = resolve_activity_health([_timer(CONV)], _payload(), _sibling_ok(CONV))
    h = out[CONV]
    assert h["status"] == "healthy"
    assert h["source"] == "systemd"
    assert h["deep_probe"] is True


def test_no_deep_probe_running_clean_is_healthy_but_shallow():
    """Unmapped timer + sibling exited 0 → healthy but deep_probe False (the
    running-but-output-unverified blind spot)."""
    out = resolve_activity_health([_timer(SKILL)], _payload(), _sibling_ok(SKILL))
    h = out[SKILL]
    assert h["status"] == "healthy"
    assert h["source"] == "systemd"
    assert h["deep_probe"] is False


def test_timer_absent_sibling_is_no_probe():
    """Sibling service entirely absent from systemctl → no_probe, never green."""
    out = resolve_activity_health([_timer(SKILL)], _payload(), {})
    assert out[SKILL]["status"] == "no_probe"
    assert out[SKILL]["source"] == "systemd"


def test_timer_loaded_but_never_ran_is_no_probe_not_green():
    """THE false-green trap (adversarial-review blocker): a loaded oneshot that has
    never run this boot reports Result=success/ExecMainStatus=0 but monotonic=0.
    Must resolve to no_probe — NOT bright green."""
    out = resolve_activity_health([_timer(SKILL)], _payload(), _sibling_never_ran(SKILL))
    assert out[SKILL]["status"] == "no_probe"


def test_failed_timer_run_is_critical():
    sib = {SKILL[:-6] + ".service": {
        "Result": "exit-code", "ExecMainStatus": "1", "ExecMainStartTimestampMonotonic": "999",
    }}
    out = resolve_activity_health([_timer(SKILL)], _payload(), sib)
    assert out[SKILL]["status"] == "critical"


def test_report_timer_reads_shared_triggered_service():
    """Report timers trigger ONE shared service; the resolver must read that unit
    (the row's `triggers`), not the by-name sibling (which never runs)."""
    shared = "universal-agent-proactive-report.service"
    row = {**_timer(R_MORNING), "triggers": shared}
    sib = {shared: {"Result": "success", "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "777"}}
    out = resolve_activity_health([row], _payload(), sib)
    assert out[R_MORNING]["status"] == "healthy"
    assert out[R_MORNING]["source"] == "systemd"


def test_none_or_nondict_row_does_not_raise():
    """Pure + total contract: a malformed list must not raise."""
    out = resolve_activity_health([None, {"group": "timers"}, {"unit": None}, 42], _payload(), {})
    assert out == {}


def test_probe_error_on_subtargeted_invariant_escalates():
    """A crashed csi_source_liveness probe (string observed_value) cannot vouch for
    the youtube pollers — it must escalate, not silently stay systemd-green."""
    perr = {
        "finding_id": "invariant:csi_source_liveness_probe_error",
        "metric_key": "csi_source_liveness_probe_error",
        "severity": "warn",
        "observed_value": "RuntimeError: probe boom",  # string, not a dict
        "recommendation": "probe crashed",
    }
    out = resolve_activity_health([_timer(GOLD)], _payload(perr), _sibling_ok(GOLD))
    assert out[GOLD]["status"] == "degraded"
    assert out[GOLD]["source"] == "invariant"


def test_service_failed_is_critical():
    out = resolve_activity_health([_service(DISCORD, active_state="failed")], _payload(), {})
    h = out[DISCORD]
    assert h["status"] == "critical"
    assert h["source"] == "systemd"


def test_service_flapping_is_degraded():
    out = resolve_activity_health([_service(DISCORD, n_restarts="5")], _payload(), {})
    assert out[DISCORD]["status"] == "degraded"


def test_csi_source_subtarget_escalates_only_matching_source():
    """csi_source_liveness listing youtube_channel_rss escalates the youtube pollers."""
    f = _finding("csi_source_liveness", "critical",
                 observed_value={"stale_sources": [{"source": "youtube_channel_rss"}]})
    out = resolve_activity_health([_timer(GOLD), _timer(PLAYLIST)], _payload(f),
                                  {**_sibling_ok(GOLD), **_sibling_ok(PLAYLIST)})
    assert out[GOLD]["status"] == "critical"
    assert out[PLAYLIST]["status"] == "critical"


def test_csi_source_subtarget_ignores_other_source():
    """A stale source that is NOT youtube → the youtube pollers stay systemd-healthy."""
    f = _finding("csi_source_liveness", "critical",
                 observed_value={"stale_sources": [{"source": "threads_owned"}]})
    out = resolve_activity_health([_timer(GOLD)], _payload(f), _sibling_ok(GOLD))
    assert out[GOLD]["status"] == "healthy"
    assert out[GOLD]["source"] == "systemd"


def test_reports_trio_escalates_only_missing_period():
    """trio with periods_missing=['midday'] → midday degraded; morning/afternoon green."""
    f = _finding("proactive_reports_daily_trio", "warn",
                 observed_value={"periods_present": ["morning", "afternoon"],
                                 "periods_missing": ["midday"]})
    acts = [_timer(R_MORNING), _timer(R_MIDDAY), _timer(R_AFTERNOON)]
    sib = {**_sibling_ok(R_MORNING), **_sibling_ok(R_MIDDAY), **_sibling_ok(R_AFTERNOON)}
    out = resolve_activity_health(acts, _payload(f), sib)
    assert out[R_MIDDAY]["status"] == "degraded"
    assert out[R_MORNING]["status"] == "healthy"
    assert out[R_AFTERNOON]["status"] == "healthy"


def test_cron_overlay_escalates_by_pipeline():
    """A cron-aggregate failure for morning_briefing escalates that row even when
    its dedicated freshness invariant is clean."""
    f = _finding("cron_consecutive_failures", "critical",
                 observed_value={"streaks": [{"task_id": "cron:morning_briefing"}]})
    out = resolve_activity_health([_timer(MORNING)], _payload(f), _sibling_ok(MORNING))
    assert out[MORNING]["status"] == "critical"
    assert out[MORNING]["finding_id"] == "cron_consecutive_failures"


def test_critical_invariant_wins_over_systemd_healthy():
    f = _finding("csi_convergence_sync_freshness", "critical")
    out = resolve_activity_health([_timer(CONV)], _payload(f), _sibling_ok(CONV))
    assert out[CONV]["status"] == "critical"


def test_row_without_unit_is_skipped():
    out = resolve_activity_health([{"group": "timers"}], _payload(), {})
    assert out == {}


def test_inprocess_enabled_is_no_probe():
    h = inprocess_health({"key": "heartbeat", "label": "Heartbeat loop", "enabled": True})
    assert h["status"] == "no_probe"
    assert h["deep_probe"] is False


def test_inprocess_disabled_is_unknown():
    h = inprocess_health({"key": "cron", "label": "Cron", "enabled": False, "env_var": "UA_DISABLE_CRON"})
    assert h["status"] == "unknown"
