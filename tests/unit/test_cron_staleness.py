"""Tests for the universal cron staleness invariant.

Background: P0a (PR #395) populated `crons[]` in the sidecar — Layer 1 of
the watchdog can now SEE every production cron's last_run_at. P1b adds
Layer 2's matching invariant: one probe that walks every enabled cron,
computes expected interval from cron_expr, and fires when last_run is
>2× past its expected gap. One invariant covers all 22 production crons
in one sweep instead of 22 individual probes.

Failure modes detected:
1. Cron stopped firing (last_run > 2× expected interval).
2. Cron is firing but consistently erroring (last_outcome != success).
3. Cron has never run AND its first scheduled time is in the past.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import time

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_registry_for_tests()
    from universal_agent.services.invariants import cron_staleness
    importlib.reload(cron_staleness)
    yield
    clear_registry_for_tests()


def _cron_dict(
    job_id: str,
    *,
    cron_expr: str,
    enabled: bool = True,
    last_run_at: float | None = None,
    last_outcome: str | None = "success",
    next_run_at: float | None = None,
) -> dict:
    """Mimic the shape gateway_server passes (epoch seconds for *_at fields)."""
    return {
        "job_id": job_id,
        "enabled": enabled,
        "cron_expr": cron_expr,
        "last_run_at": last_run_at,
        "last_outcome": last_outcome,
        "next_run_at": next_run_at,
    }


def _now_epoch() -> float:
    return time.time()


def test_registers_on_import() -> None:
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert "cron_staleness" in ids


def test_all_fresh_crons_emit_nothing() -> None:
    """Every cron ran within 2× its expected interval → no finding."""
    crons = [
        # Hourly cron, ran 30 min ago
        _cron_dict("hourly_job", cron_expr="0 * * * *", last_run_at=_now_epoch() - 1800),
        # Every-minute cron, ran 30 sec ago
        _cron_dict("minute_job", cron_expr="*/1 * * * *", last_run_at=_now_epoch() - 30),
        # Daily cron, ran 6h ago (within 2-day = 48h threshold)
        _cron_dict("daily_job", cron_expr="0 6 * * *", last_run_at=_now_epoch() - 21600),
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert matches == []


def test_one_stale_cron_fires() -> None:
    """A cron that ran 5h ago when expected hourly → fires."""
    crons = [
        _cron_dict("good_job", cron_expr="*/1 * * * *", last_run_at=_now_epoch() - 30),
        _cron_dict(
            "stuck_job", cron_expr="0 * * * *",
            last_run_at=_now_epoch() - 18000,  # 5h ago, hourly cron, way past 2h threshold
        ),
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    stale_jobs = {s["job_id"] for s in obs.get("stale_crons") or []}
    assert "stuck_job" in stale_jobs
    assert "good_job" not in stale_jobs


def test_multiple_stale_crons_in_one_finding() -> None:
    """Three stale crons → ONE finding listing all three (not three separate)."""
    crons = [
        _cron_dict("good", cron_expr="*/1 * * * *", last_run_at=_now_epoch() - 30),
        _cron_dict("stale_a", cron_expr="0 * * * *", last_run_at=_now_epoch() - 18000),
        _cron_dict("stale_b", cron_expr="*/15 * * * *", last_run_at=_now_epoch() - 7200),  # 2h, 15min cron
        _cron_dict("stale_c", cron_expr="0 6 * * *", last_run_at=_now_epoch() - 432000),  # 5 days, daily
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    stale_jobs = {s["job_id"] for s in obs.get("stale_crons") or []}
    assert stale_jobs == {"stale_a", "stale_b", "stale_c"}


def test_disabled_crons_ignored() -> None:
    """A disabled cron with stale last_run is NOT a finding (operator opted
    out of running it)."""
    crons = [
        _cron_dict(
            "disabled", cron_expr="0 * * * *", enabled=False,
            last_run_at=_now_epoch() - 432000,  # 5 days old, would otherwise be stale
        ),
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert matches == []


def test_error_outcome_fires_even_with_recent_last_run() -> None:
    """A cron firing on schedule but its last outcome was an error → fires.
    Don't make the operator wait for stale-timing — surface the error now."""
    crons = [
        _cron_dict(
            "erroring", cron_expr="*/5 * * * *",
            last_run_at=_now_epoch() - 60,  # ran 1 min ago, would be fresh
            last_outcome="error",
        ),
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    stale = obs.get("stale_crons") or []
    assert any(s["job_id"] == "erroring" and s.get("reason") == "last_outcome_error" for s in stale)


def test_never_run_with_past_next_run_fires() -> None:
    """A cron that has never run AND its first scheduled time was in the
    past (cron was registered hours ago but hasn't fired) → fires."""
    crons = [
        _cron_dict(
            "never_fired", cron_expr="0 * * * *",
            last_run_at=None,
            next_run_at=_now_epoch() - 7200,  # next_run was 2h ago, never fired
        ),
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert len(matches) == 1


def test_never_run_with_future_next_run_stays_quiet() -> None:
    """A freshly-added cron whose first scheduled time is still in the
    future → silent (not yet expected to have run)."""
    crons = [
        _cron_dict(
            "new_cron", cron_expr="0 6 * * *",
            last_run_at=None,
            next_run_at=_now_epoch() + 3600,  # next_run in 1h
        ),
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert matches == []


def test_empty_cron_list_silent() -> None:
    findings = run_invariants({"cron_jobs": []})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert matches == []


def test_missing_cron_jobs_key_silent() -> None:
    findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    assert matches == []


def test_malformed_cron_expr_does_not_crash() -> None:
    """If croniter can't parse the cron_expr, skip that row gracefully —
    never crash the watchdog over one bad row."""
    crons = [
        _cron_dict("good", cron_expr="*/1 * * * *", last_run_at=_now_epoch() - 30),
        _cron_dict("garbage", cron_expr="this is not a cron", last_run_at=_now_epoch() - 100000),
    ]
    findings = run_invariants({"cron_jobs": crons})
    # No probe-error (the invariant should swallow per-row parse failures).
    probe_errors = [f for f in findings if "probe_error" in (f.metric_key or "")]
    assert probe_errors == []


def test_iso_string_last_run_at_supported() -> None:
    """Some cron rows arrive with ISO-string timestamps (e.g. from the
    summarizer). The invariant should handle both epoch-float and ISO."""
    one_hour_ago = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    crons = [
        _cron_dict("iso_cron", cron_expr="0 * * * *", last_run_at=one_hour_ago),
    ]
    findings = run_invariants({"cron_jobs": crons})
    matches = [f for f in findings if f.metric_key == "cron_staleness"]
    # 5h since last run on an hourly cron → fires (5h > 2h threshold)
    assert len(matches) == 1
