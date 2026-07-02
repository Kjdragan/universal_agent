"""Tests for the convergence_change_monitor verification upgrades.

Covers the per-window per-tier 429 buckets, the before-window coverage guard
(the events file is line-trimmed, so an incomplete baseline must NOT be allowed
to fake an improvement), and the verdict's refusal to claim improvement under
truncation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time

import pytest

from universal_agent.scripts.convergence_change_monitor import (
    _analyze_429,
    _render,
    _verdict,
)


def _write_events(path, events):
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _ev(ts, category, model=None, fup_texted=False, caller="universal_agent/services/x.py"):
    e = {"ts": ts, "category": category, "caller": caller, "fup_texted": fup_texted}
    if model is not None:
        e["model"] = model
    return e


@pytest.fixture
def events_file(tmp_path, monkeypatch):
    path = tmp_path / "zai_inference_events.jsonl"
    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", str(path))
    return path


def test_per_tier_buckets_collapse_model_to_tier(events_file):
    """models collapse to limiter tiers; before/after totals + 429s per tier."""
    now = float(int(time.time()))  # integral base: datetime.fromtimestamp microsecond rounding must not move window boundaries
    change_ts = now - 600  # change 10 min ago; before window = [change-600, change]
    change_at = datetime.fromtimestamp(change_ts, tz=timezone.utc)

    events = []
    # BEFORE window — fill from before_lo so the coverage guard does NOT trip.
    for i in range(600):
        events.append(_ev(change_ts - 600 + i, "ok", model="glm-5-turbo"))
    for i in range(200):
        events.append(_ev(change_ts - 590 + i, "rate_limited_429", model="glm-5-turbo", fup_texted=True))
    # haiku traffic in the before window
    for i in range(40):
        events.append(_ev(change_ts - 500 + i, "ok", model="glm-4.5-air"))
    for i in range(10):
        events.append(_ev(change_ts - 500 + i, "rate_limited_429", model="glm-4.5-air"))
    # AFTER window — much lower sonnet 429 rate
    for i in range(600):
        events.append(_ev(now - 600 + i, "ok", model="glm-5-turbo"))
    for i in range(12):
        events.append(_ev(now - 590 + i, "rate_limited_429", model="glm-5-turbo", fup_texted=True))
    _write_events(events_file, events)

    z = _analyze_429(change_at)
    assert z["available"] is True
    assert z["before_truncated"] is False

    bt = z["before"]["tiers"]
    at = z["after"]["tiers"]
    # sonnet (glm-5-turbo) tier present both sides with denominators.
    assert bt["sonnet"]["r429"] == 200
    assert bt["sonnet"]["total"] == 800  # 600 ok + 200 r429
    assert at["sonnet"]["r429"] == 12
    # haiku (glm-4.5-air) collapsed correctly in the before window.
    assert bt["haiku"]["r429"] == 10
    assert bt["haiku"]["total"] == 50

    # fup_texted-429 counted per window (gradient throttle visibility).
    assert z["before"]["fup_texted_429"] == 200
    assert z["after"]["fup_texted_429"] == 12


def test_unknown_tier_for_pre_deploy_events(events_file):
    """Events without a `model` field land in the explicit `unknown` tier."""
    now = float(int(time.time()))  # integral base: datetime.fromtimestamp microsecond rounding must not move window boundaries
    change_ts = now - 300
    change_at = datetime.fromtimestamp(change_ts, tz=timezone.utc)
    events = []
    for i in range(300):
        events.append(_ev(change_ts - 300 + i, "ok"))  # no model -> unknown
    for i in range(50):
        events.append(_ev(change_ts - 290 + i, "rate_limited_429"))  # no model
    for i in range(300):
        events.append(_ev(now - 300 + i, "ok", model="glm-5-turbo"))
    _write_events(events_file, events)

    z = _analyze_429(change_at)
    assert "unknown" in z["before"]["tiers"]
    assert z["before"]["tiers"]["unknown"]["r429"] == 50
    # The render must call out an unknown-dominated window.
    body = _render(change_at, z, {"keeping_up": True, "newest_candidate_at": "x",
                                  "candidate_lag_hours": 1, "newest_signature_at": "x",
                                  "signature_lag_hours": 1, "hours_since_change": 1,
                                  "signatures_since_change": 1, "signatures_per_hour": 1,
                                  "candidates_since_change": 1, "candidates_per_hour": 1},
                   "headline")
    assert "dominated by un-tagged" in body


def test_coverage_guard_trips_when_before_window_truncated(events_file):
    """If the before bucket's earliest event starts well after before_lo (file
    line-trimmed), the guard flag trips and the verdict refuses improvement."""
    now = float(int(time.time()))  # integral base: datetime.fromtimestamp microsecond rounding must not move window boundaries
    change_ts = now - 3600  # 1h window
    change_at = datetime.fromtimestamp(change_ts, tz=timezone.utc)
    events = []
    # BEFORE events only in the LAST ~5 min of the 60-min before window — i.e.
    # the baseline is truncated, min_ts is way after before_lo.
    for i in range(80):
        events.append(_ev(change_ts - 300 + i, "ok", model="glm-5-turbo"))
    for i in range(80):
        events.append(_ev(change_ts - 290 + i, "rate_limited_429", model="glm-5-turbo"))
    # AFTER window — looks clean (low rate).
    for i in range(600):
        events.append(_ev(now - 3600 + i, "ok", model="glm-5-turbo"))
    for i in range(5):
        events.append(_ev(now - 590 + i, "rate_limited_429", model="glm-5-turbo"))
    _write_events(events_file, events)

    z = _analyze_429(change_at)
    assert z["before_truncated"] is True

    action, headline = _verdict(z, {"keeping_up": True})
    assert action == "ACTION"
    assert "TRUNCATED" in headline

    body = _render(change_at, z, {"keeping_up": True, "newest_candidate_at": "x",
                                  "candidate_lag_hours": 1, "newest_signature_at": "x",
                                  "signature_lag_hours": 1, "hours_since_change": 1,
                                  "signatures_since_change": 1, "signatures_per_hour": 1,
                                  "candidates_since_change": 1, "candidates_per_hour": 1},
                   headline)
    assert "DO NOT TRUST" in body


def test_verdict_allows_improvement_when_not_truncated(events_file):
    """Sanity: a complete baseline with a real drop yields the FYI/improved path."""
    now = float(int(time.time()))  # integral base: datetime.fromtimestamp microsecond rounding must not move window boundaries
    change_ts = now - 600
    change_at = datetime.fromtimestamp(change_ts, tz=timezone.utc)
    events = []
    for i in range(600):
        events.append(_ev(change_ts - 600 + i, "ok", model="glm-5-turbo"))
    for i in range(300):  # 33% rejection before
        events.append(_ev(change_ts - 590 + i, "rate_limited_429", model="glm-5-turbo"))
    for i in range(600):
        events.append(_ev(now - 600 + i, "ok", model="glm-5-turbo"))
    for i in range(10):  # ~1.6% rejection after
        events.append(_ev(now - 590 + i, "rate_limited_429", model="glm-5-turbo"))
    _write_events(events_file, events)

    z = _analyze_429(change_at)
    assert z["before_truncated"] is False
    action, headline = _verdict(z, {"keeping_up": True})
    assert action == "FYI"
    assert "looks good" in headline
