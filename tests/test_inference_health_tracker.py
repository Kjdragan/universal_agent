"""Tests for the inference-health circuit breaker (VP dispatch hold).

Covers the four behaviors the breaker must guarantee:
  (a) N failures in-window trips the hold, dispatch is skipped, exactly ONE
      alert is emitted for the episode.
  (b) below threshold, dispatch proceeds (no hold).
  (c) the hold auto-clears after the hold window elapses.
  (d) with UA_INFERENCE_DEGRADE_ENABLED=false the breaker never holds
      (legacy behavior).
"""

from __future__ import annotations

import pytest

from universal_agent.services.inference_health_tracker import (
    InferenceHealthTracker,
)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Fresh singleton + armed breaker (enabled) around each test."""
    monkeypatch.setenv("UA_INFERENCE_DEGRADE_ENABLED", "true")
    InferenceHealthTracker.reset_instance()
    yield
    InferenceHealthTracker.reset_instance()


def _make(**kwargs) -> InferenceHealthTracker:
    return InferenceHealthTracker.get_instance(
        fail_count=kwargs.get("fail_count", 5),
        window_minutes=kwargs.get("window_minutes", 10.0),
        hold_minutes=kwargs.get("hold_minutes", 10.0),
    )


# (a) N failures in-window trips the hold + exactly one alert ─────────────────

def test_threshold_failures_trip_hold_with_single_alert():
    t = _make(fail_count=5, window_minutes=10.0, hold_minutes=10.0)
    now = 1_000_000.0

    # 4 failures — still below threshold, no hold.
    for i in range(4):
        t.record_failure("5xx", now=now + i)
    assert t.should_hold_dispatch(now=now + 4).hold is False

    # 5th failure crosses the threshold.
    t.record_failure("5xx", now=now + 5)

    d1 = t.should_hold_dispatch(now=now + 6)
    assert d1.hold is True
    assert d1.alert is not None  # the ONE alert for this episode
    assert d1.alert["fail_count"] >= 5
    assert d1.alert["reason"] == "inference_degraded"

    # Subsequent held ticks keep holding but do NOT re-alert.
    d2 = t.should_hold_dispatch(now=now + 7)
    assert d2.hold is True
    assert d2.alert is None

    d3 = t.should_hold_dispatch(now=now + 8)
    assert d3.hold is True
    assert d3.alert is None


# (b) below threshold → dispatch proceeds ────────────────────────────────────

def test_below_threshold_allows_dispatch():
    t = _make(fail_count=5)
    now = 2_000_000.0
    for i in range(4):
        t.record_failure("timeout", now=now + i)
    d = t.should_hold_dispatch(now=now + 5)
    assert d.hold is False
    assert d.alert is None
    assert d.fail_count == 4


def test_failures_outside_window_do_not_count():
    t = _make(fail_count=5, window_minutes=10.0)
    now = 3_000_000.0
    # 5 failures, but spread so the oldest fall outside the 10-min window at
    # evaluation time → fewer than threshold remain in-window.
    for i in range(5):
        t.record_failure("429", now=now + i * 200)  # 200s apart → span 800s
    # Evaluate far enough ahead that only the last couple remain in window.
    d = t.should_hold_dispatch(now=now + 5 * 200 + 500)
    assert d.hold is False


# (c) hold auto-clears after the window ───────────────────────────────────────

def test_hold_auto_clears_after_window():
    t = _make(fail_count=5, window_minutes=10.0, hold_minutes=10.0)
    now = 4_000_000.0
    for i in range(5):
        t.record_failure("overloaded", now=now + i)

    # Trip the hold.
    assert t.should_hold_dispatch(now=now + 6).hold is True
    # Still holding partway through the hold window.
    assert t.should_hold_dispatch(now=now + 300).hold is True
    # After hold_minutes (600s) elapse, dispatch auto-resumes. The original
    # failures are also now outside the 10-min window, so no immediate re-trip.
    d = t.should_hold_dispatch(now=now + 6 + 601)
    assert d.hold is False

    # A fresh breach after auto-resume is a NEW episode → alerts again.
    base = now + 6 + 601
    for i in range(5):
        t.record_failure("503", now=base + i)
    d2 = t.should_hold_dispatch(now=base + 6)
    assert d2.hold is True
    assert d2.alert is not None


# (d) disabled → never holds ──────────────────────────────────────────────────

def test_disabled_never_holds(monkeypatch):
    monkeypatch.setenv("UA_INFERENCE_DEGRADE_ENABLED", "false")
    t = _make(fail_count=5)
    now = 5_000_000.0
    for i in range(20):
        t.record_failure("5xx", now=now + i)
    d = t.should_hold_dispatch(now=now + 21)
    assert d.hold is False
    assert d.alert is None
