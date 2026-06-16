"""M4 — selective cron→heartbeat coupling + debounce.

Covers the two coupling lanes:
  - gateway_server.py::_maybe_wake_heartbeat_after_autonomous_cron (the
    autonomous-cron coupling, the measured ~62/hr driver), and
  - cron_service.py::CronService._maybe_wake_heartbeat (the session-bound
    metadata.wake_heartbeat back door).

The default-deny allowlist must stop a non-allowlisted autonomous cron from
waking Simone, an allowlisted cron must still wake (debounced), the master
escape-hatch flag must revert to the old wake-on-every-cron behavior, and the
urgent ``request_heartbeat_now`` path must never be touched by the coupling.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from universal_agent import gateway_server
from universal_agent.cron_service import (
    CronJob,
    CronService,
    coupling_wake_allowed_jobs,
    coupling_wake_selective_enabled,
)


# ──────────────────────────────────────────────────────────────────────────
# Gateway autonomous-cron coupling
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def wakeable(monkeypatch):
    """Hold every non-M4 gate of _maybe_wake_heartbeat_after_autonomous_cron in
    the 'would-wake' state, so a test exercises only the new allowlist+debounce.
    Returns the heartbeat-service mock that records wake calls."""
    hb = MagicMock()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", hb)
    monkeypatch.setattr(gateway_server, "_autonomous_cron_to_heartbeat_enabled", lambda: True)
    monkeypatch.setattr(gateway_server, "_task_hub_has_dispatch_eligible_items", lambda: True)
    monkeypatch.setattr(
        gateway_server,
        "_collect_live_heartbeat_targets",
        lambda **kw: {"daemon_simone_heartbeat"},
    )
    adm = MagicMock()
    adm.admit.return_value = SimpleNamespace(
        action="start_new_run", run_id="r1", attempt_id="a1"
    )
    monkeypatch.setattr(gateway_server, "_workflow_admission_service", lambda: adm)
    # Reset the debounce baseline so the first eligible call is never throttled.
    # A fixed far-in-the-past sentinel, NOT 0.0: the debounce gate is
    # (time.monotonic() - baseline) < interval, and on a freshly-provisioned CI
    # runner time.monotonic() (CLOCK_MONOTONIC since VM boot) is only seconds old,
    # so a 0.0 baseline makes (monotonic() - 0.0) < 300 true and wrongly throttles
    # the first wake - exactly the PR #1049 CI failure (run 27595042466). -10_000
    # is always more than one debounce window in the past for any non-negative
    # monotonic clock, and (unlike time.monotonic() - N) it does not snapshot "now",
    # so it stays correct when a test later pins time.monotonic() to a small value.
    monkeypatch.setattr(gateway_server, "_last_cron_coupled_wake_at", -10_000.0)
    return hb


def _wake(system_job: str, *, reason: str = "cron_autonomous_run:run1"):
    gateway_server._maybe_wake_heartbeat_after_autonomous_cron(
        run_status="success",
        is_autonomous=True,
        reason=reason,
        system_job=system_job,
    )


def test_empty_system_job_does_not_wake(wakeable):
    # The common case: housekeeping cron, empty allowlist → default-deny.
    _wake("")
    wakeable.request_heartbeat_next.assert_not_called()


def test_disallowed_job_does_not_wake(wakeable, monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "some_other_cron")
    _wake("simone_chat_auto_complete")
    wakeable.request_heartbeat_next.assert_not_called()


def test_allowlisted_job_wakes(wakeable, monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "needs_simone_cron")
    _wake("needs_simone_cron")
    wakeable.request_heartbeat_next.assert_called_once_with(
        "daemon_simone_heartbeat", reason="cron_autonomous_run:run1"
    )
    # Invariant #1: the coupling never touches the urgent now-path.
    wakeable.request_heartbeat_now.assert_not_called()



def test_allowlisted_job_wakes_on_fresh_monotonic_clock(wakeable, monkeypatch):
    # Regression guard for the PR #1049 CI failure (run 27595042466): on a
    # freshly-provisioned host time.monotonic() (CLOCK_MONOTONIC since VM boot)
    # is smaller than the 300s debounce window. The fixture baseline must still
    # let the first eligible wake fire - a 0.0 baseline reproduces the failure
    # because time.monotonic() - 0.0 < 300 throttles the wake and
    # request_heartbeat_next is never called.
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "needs_simone_cron")
    monkeypatch.setattr(gateway_server.time, "monotonic", lambda: 50.0)
    _wake("needs_simone_cron")
    wakeable.request_heartbeat_next.assert_called_once_with(
        "daemon_simone_heartbeat", reason="cron_autonomous_run:run1"
    )


def test_allowlist_whitespace_is_tolerated(wakeable, monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", " a , needs_simone_cron , b ")
    _wake("needs_simone_cron")
    wakeable.request_heartbeat_next.assert_called_once()


def test_debounce_blocks_second_wake(wakeable, monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "needs_simone_cron")
    # default debounce 300s; two back-to-back fires collapse to one wake.
    _wake("needs_simone_cron", reason="cron_autonomous_run:run1")
    _wake("needs_simone_cron", reason="cron_autonomous_run:run2")
    assert wakeable.request_heartbeat_next.call_count == 1


def test_debounce_disabled_allows_second(wakeable, monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "needs_simone_cron")
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_MIN_INTERVAL_SECONDS", "0")
    _wake("needs_simone_cron", reason="cron_autonomous_run:run1")
    _wake("needs_simone_cron", reason="cron_autonomous_run:run2")
    assert wakeable.request_heartbeat_next.call_count == 2


def test_selective_off_reverts_to_wake_all(wakeable, monkeypatch):
    # Escape hatch: flip the master flag off → pre-M4 wake-on-every-cron behavior
    # even for a non-allowlisted job.
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_SELECTIVE", "0")
    _wake("simone_chat_auto_complete")
    wakeable.request_heartbeat_next.assert_called_once()


def test_selective_off_is_a_full_revert_including_debounce(wakeable, monkeypatch):
    # The kill switch must be a TRUE revert: with selective off, the debounce is
    # bypassed too, so two back-to-back fires both wake (pre-M4 had no debounce).
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_SELECTIVE", "0")
    _wake("simone_chat_auto_complete", reason="r1")
    _wake("simone_chat_auto_complete", reason="r2")
    assert wakeable.request_heartbeat_next.call_count == 2


def test_deny_returns_before_admission(wakeable, monkeypatch):
    # Invariant: a non-allowlisted deny is cheap — it returns BEFORE the (I/O-ish)
    # workflow-admission service is consulted.
    adm = MagicMock()
    monkeypatch.setattr(gateway_server, "_workflow_admission_service", lambda: adm)
    _wake("not_allowlisted_cron")
    adm.admit.assert_not_called()
    wakeable.request_heartbeat_next.assert_not_called()


def test_debounce_not_committed_without_a_real_wake(wakeable, monkeypatch):
    # A fire that passes the gates but finds no live target must NOT burn the
    # debounce window — otherwise a later legitimate wake is stranded for ~300s.
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "needs_simone_cron")
    targets: dict = {"value": set()}
    monkeypatch.setattr(
        gateway_server, "_collect_live_heartbeat_targets", lambda **kw: targets["value"]
    )
    _wake("needs_simone_cron", reason="r1")  # no live target → no wake, no commit
    assert wakeable.request_heartbeat_next.call_count == 0
    targets["value"] = {"daemon_simone_heartbeat"}
    _wake("needs_simone_cron", reason="r2")  # within 300s, but window was never committed
    assert wakeable.request_heartbeat_next.call_count == 1


def test_non_success_does_not_wake(wakeable, monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "needs_simone_cron")
    gateway_server._maybe_wake_heartbeat_after_autonomous_cron(
        run_status="failed", is_autonomous=True, reason="r", system_job="needs_simone_cron"
    )
    wakeable.request_heartbeat_next.assert_not_called()


def test_non_autonomous_does_not_wake(wakeable, monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "needs_simone_cron")
    gateway_server._maybe_wake_heartbeat_after_autonomous_cron(
        run_status="success", is_autonomous=False, reason="r", system_job="needs_simone_cron"
    )
    wakeable.request_heartbeat_next.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────
# Session-bound coupling (cron_service.py::_maybe_wake_heartbeat)
# ──────────────────────────────────────────────────────────────────────────
def _session_cron_service():
    """A CronService with only the wake_callback wired (no DB construction)."""
    service = CronService.__new__(CronService)
    service.wake_callback = MagicMock()
    return service


def _job(metadata: dict) -> CronJob:
    return CronJob(
        job_id="job1",
        user_id="cron:job1",
        workspace_dir="/tmp/ws",
        command="echo hi",
        metadata=metadata,
    )


def test_session_bound_autonomous_next_not_allowlisted_blocked(monkeypatch):
    monkeypatch.delenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", raising=False)
    svc = _session_cron_service()
    job = _job(
        {
            "wake_heartbeat": True,
            "session_id": "sess1",
            "autonomous": True,
            "system_job": "simone_chat_auto_complete",
        }
    )
    svc._maybe_wake_heartbeat(job, "reason")
    svc.wake_callback.assert_not_called()


def test_session_bound_non_autonomous_still_wakes(monkeypatch):
    monkeypatch.delenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", raising=False)
    svc = _session_cron_service()
    # No "autonomous" flag → legitimate user/email-scheduled session wake; intact.
    job = _job({"wake_heartbeat": True, "session_id": "sess1"})
    svc._maybe_wake_heartbeat(job, "reason")
    svc.wake_callback.assert_called_once()
    assert svc.wake_callback.call_args.args[1] == "next"


def test_session_bound_autonomous_now_still_wakes(monkeypatch):
    monkeypatch.delenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", raising=False)
    svc = _session_cron_service()
    # Explicit wake_mode="now" → urgent-by-opt-in; the gate only touches "next".
    job = _job(
        {
            "wake_heartbeat": True,
            "session_id": "sess1",
            "autonomous": True,
            "system_job": "some_cron",
            "wake_mode": "now",
        }
    )
    svc._maybe_wake_heartbeat(job, "reason")
    svc.wake_callback.assert_called_once()
    assert svc.wake_callback.call_args.args[1] == "now"


def test_session_bound_autonomous_allowlisted_wakes(monkeypatch):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", "allowed_cron")
    svc = _session_cron_service()
    job = _job(
        {
            "wake_heartbeat": True,
            "session_id": "sess1",
            "autonomous": True,
            "system_job": "allowed_cron",
        }
    )
    svc._maybe_wake_heartbeat(job, "reason")
    svc.wake_callback.assert_called_once()


def test_session_bound_autonomous_blocked_unless_selective(monkeypatch):
    monkeypatch.delenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", raising=False)
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_SELECTIVE", "0")
    svc = _session_cron_service()
    job = _job(
        {
            "wake_heartbeat": True,
            "session_id": "sess1",
            "autonomous": True,
            "system_job": "some_cron",
        }
    )
    svc._maybe_wake_heartbeat(job, "reason")
    # Master flag off → revert to old behavior, the autonomous next-wake fires.
    svc.wake_callback.assert_called_once()


def test_session_bound_autonomous_missing_system_job_blocked(monkeypatch):
    # Production shape for a cron with no system_job key (autonomous=True): still
    # default-denied (system_job resolves to "" which is not in the empty allowlist).
    monkeypatch.delenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", raising=False)
    svc = _session_cron_service()
    job = _job({"wake_heartbeat": True, "session_id": "sess1", "autonomous": True})
    svc._maybe_wake_heartbeat(job, "reason")
    svc.wake_callback.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────
# Policy-helper parsing (pin the fail directions — a botched env value must not
# silently flip M4 off in the dangerous direction unnoticed)
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", set()),
        ("   ", set()),
        (",,,", set()),
        (" , , ", set()),
        ("a", {"a"}),
        ("a,a,b", {"a", "b"}),
        (" a , b ,a ", {"a", "b"}),
    ],
)
def test_coupling_wake_allowed_jobs_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", raw)
    assert coupling_wake_allowed_jobs() == frozenset(expected)


def test_coupling_wake_allowed_jobs_unset_is_empty(monkeypatch):
    monkeypatch.delenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST", raising=False)
    assert coupling_wake_allowed_jobs() == frozenset()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("On", True),
        (" yes ", True),
        ("0", False),
        ("off", False),
        # Documented fail direction: garbage / typos parse as DISABLED (M4 off →
        # reverts to wake-all). This is the operational footgun the reviewer
        # flagged; pinning it makes the fail-open a conscious, locked-in choice.
        ("2", False),
        ("maybe", False),
        ("", False),
    ],
)
def test_coupling_wake_selective_enabled_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("UA_CRON_HEARTBEAT_WAKE_SELECTIVE", raw)
    assert coupling_wake_selective_enabled() is expected


def test_coupling_wake_selective_default_on(monkeypatch):
    monkeypatch.delenv("UA_CRON_HEARTBEAT_WAKE_SELECTIVE", raising=False)
    assert coupling_wake_selective_enabled() is True
