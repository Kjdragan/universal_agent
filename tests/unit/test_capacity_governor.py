"""Tests for CapacityGovernor.can_dispatch, focused on R1's new "Check -1":
the operator control plane (services/zai_control) gate that stands down ALL
dispatch — including in-process SDK principals invisible to the httpx
observability hook — while a global pause (most notably the auto-1310
weekly/monthly quota-exhaustion pause) is active.
"""

from __future__ import annotations

import time

import pytest

from universal_agent.services import zai_control
from universal_agent.services.capacity_governor import CapacityGovernor


@pytest.fixture(autouse=True)
def _fresh_governor():
    CapacityGovernor.reset_instance()
    yield
    CapacityGovernor.reset_instance()


@pytest.fixture
def isolated_control(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ZAI_CONTROL_PATH", str(tmp_path / "zai_control.json"))
    zai_control._invalidate_cache()
    yield tmp_path / "zai_control.json"
    zai_control._invalidate_cache()


def test_can_dispatch_true_when_no_control_file(isolated_control):
    """Missing control file → normal operation (fail-open), unchanged path."""
    governor = CapacityGovernor.get_instance()
    allowed, reason = governor.can_dispatch()
    assert allowed is True
    assert reason == "capacity_available"


def test_can_dispatch_false_when_globally_paused(isolated_control):
    zai_control.set_global_pause(True, ttl_seconds=3600, reason="zai_1310_weekly_limit_exhausted (reset test)")
    governor = CapacityGovernor.get_instance()
    allowed, reason = governor.can_dispatch()
    assert allowed is False
    assert reason.startswith("zai_global_pause:")
    assert "zai_1310" in reason


def test_can_dispatch_true_again_after_pause_clears(isolated_control):
    zai_control.set_global_pause(True, ttl_seconds=3600, reason="zai_1310_weekly_limit_exhausted")
    governor = CapacityGovernor.get_instance()
    assert governor.can_dispatch()[0] is False

    zai_control.clear_all()
    allowed, reason = governor.can_dispatch()
    assert allowed is True
    assert reason == "capacity_available"


def test_can_dispatch_fails_open_on_corrupt_control_file(isolated_control, monkeypatch):
    """A corrupt/unreadable control file must NOT deny dispatch — the
    governor's own pre-existing checks (backoff/slots) still apply
    unaffected."""
    isolated_control.write_text("{not valid json")
    zai_control._invalidate_cache()
    governor = CapacityGovernor.get_instance()
    allowed, reason = governor.can_dispatch()
    assert allowed is True
    assert reason == "capacity_available"


def test_can_dispatch_fails_open_when_control_module_raises(monkeypatch):
    """Even if the zai_control import/call itself explodes, can_dispatch
    must not crash or wrongly deny dispatch."""

    def boom(*a, **k):
        raise RuntimeError("control plane exploded")

    monkeypatch.setattr(zai_control, "is_globally_paused", boom)
    governor = CapacityGovernor.get_instance()
    allowed, reason = governor.can_dispatch()
    assert allowed is True
    assert reason == "capacity_available"


def test_backoff_check_still_applies_when_not_paused(isolated_control):
    """The pre-existing backoff/api-down/slot checks are unaffected by the
    new Check -1 when there is no global pause."""
    governor = CapacityGovernor.get_instance()
    governor._backoff_until = time.time() + 60
    allowed, reason = governor.can_dispatch()
    assert allowed is False
    assert reason.startswith("capacity_backoff:")
