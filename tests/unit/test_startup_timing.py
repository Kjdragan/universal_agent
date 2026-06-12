"""Unit tests for the gateway startup phase timer (C1 measure-first of the
deploy-restart resilience ADR, project_docs/06_platform/12_*).

The timer is pure and clock-injectable so we test segment accounting + the
ranked summary without real sleeps.
"""

from __future__ import annotations

from universal_agent.startup_timing import StartupPhaseTimer


def _clock(values):
    """A deterministic clock callable that yields `values` in order, repeating
    the last value once exhausted (so extra calls don't raise)."""
    state = {"i": 0}

    def _next():
        i = state["i"]
        state["i"] = min(i + 1, len(values) - 1)
        return values[i]

    return _next


def test_mark_records_segment_duration():
    # t0=0.0; mark("a") at 1.5 -> segment 1.5
    timer = StartupPhaseTimer(clock=_clock([0.0, 1.5]))
    seg = timer.mark("schema")
    assert seg == 1.5


def test_summary_ranks_slowest_first_and_reports_total():
    # t0=0.0; schema seg=0.5 (@0.5); daemon seg=2.5 (@3.0); total=3.0
    timer = StartupPhaseTimer(clock=_clock([0.0, 0.5, 3.0, 3.0]))
    timer.mark("schema")
    timer.mark("daemon_sessions")
    text = timer.summary()
    assert "3.00s" in text  # total pre-yield
    # slowest (daemon 2.5s) listed before schema (0.5s)
    assert text.index("daemon_sessions") < text.index("schema")
    assert "daemon_sessions=+2.50s" in text


def test_summary_handles_no_marks():
    timer = StartupPhaseTimer(clock=_clock([0.0, 0.0]))
    text = timer.summary()
    assert "no marks" in text.lower()
