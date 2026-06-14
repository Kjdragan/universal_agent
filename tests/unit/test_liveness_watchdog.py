"""Guard tests for the canonical liveness / no-progress kill policy
(``timeout_policy.LivenessWatchdog``).

This is the ONE shared convention behind all three UA agent-execution lanes
(in-process ``ProcessTurnAdapter.execute``, the VP SDK consumer
``consume_adapter_events_with_idle_timeout``, and the ``claude --print``
subprocess ``_monitor_cli_output``). The operator requirement (2026-06-14):
**never kill a live, working turn on an arbitrary wall-clock cap** — kill only
when genuinely stuck (no sign of life past the idle threshold while no tool is
in flight), with a very high absolute backstop for a fully-wedged process.

These tests encode that requirement directly with an injected clock so they are
deterministic and instant.
"""

from __future__ import annotations

from universal_agent.timeout_policy import LivenessWatchdog


class _Clock:
    """Deterministic injectable monotonic clock."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_idle_kill_fires_after_threshold() -> None:
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=10, now=clock)
    clock.advance(9)
    assert wd.overdue() is None
    clock.advance(2)  # 11s idle ≥ 10
    reason = wd.overdue()
    assert reason is not None and "no progress" in reason


def test_working_turn_is_never_killed_past_old_wall_clock_cap() -> None:
    """THE core operator guard: a turn that keeps emitting events is never cut
    off, even far past the old tier wall-clock cap (opus was 1800s)."""
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=600, now=clock)
    # Emit a sign of life every 100s for ~3 hours of wall-clock time.
    for _ in range(108):  # 10_800s elapsed — 6× the old opus cap
        clock.advance(100)
        wd.note_activity()
        assert wd.overdue() is None, "an actively-progressing turn must not be killed"


def test_activity_resets_idle_window() -> None:
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=10, now=clock)
    clock.advance(8)
    wd.note_activity()
    clock.advance(8)  # 16s total elapsed, but only 8s since last activity
    assert wd.overdue() is None
    clock.advance(3)  # now 11s idle
    assert wd.overdue() is not None


def test_tool_in_flight_exempts_idle_kill() -> None:
    """A long build/test emits tool_call then nothing until tool_result minutes
    later — that gap must NOT trigger the idle kill."""
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=10, now=clock)
    wd.note_activity(tool_started=True)
    clock.advance(600)  # 10 min mid-tool, way past the idle threshold
    assert wd.overdue() is None, "idle kill must be suspended while a tool runs"
    assert wd.tools_in_flight == 1
    wd.note_activity(tool_finished=True)  # tool returned; idle window re-armed
    assert wd.tools_in_flight == 0
    clock.advance(5)
    assert wd.overdue() is None
    clock.advance(6)  # 11s idle since tool_result, no tool in flight
    assert wd.overdue() is not None


def test_parallel_tools_counter_balances() -> None:
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=10, now=clock)
    wd.note_activity(tool_started=True)
    wd.note_activity(tool_started=True)
    assert wd.tools_in_flight == 2
    clock.advance(100)
    assert wd.overdue() is None
    wd.note_activity(tool_finished=True)
    clock.advance(100)
    assert wd.overdue() is None, "one tool still in flight"
    wd.note_activity(tool_finished=True)
    assert wd.tools_in_flight == 0
    clock.advance(11)
    assert wd.overdue() is not None


def test_counter_floors_at_zero_on_stray_tool_finished() -> None:
    """A stray tool_finished (or a tool_result whose tool_call was missed) must
    not drive the counter negative and silently disable the idle kill forever."""
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=10, now=clock)
    wd.note_activity(tool_finished=True)  # underflow attempt
    assert wd.tools_in_flight == 0
    clock.advance(11)
    assert wd.overdue() is not None, "idle kill must still arm after stray finish"


def test_absolute_backstop_fires_despite_tool_in_flight() -> None:
    """The backstop is the last resort for a wedged tool that never returns."""
    clock = _Clock()
    wd = LivenessWatchdog(
        idle_kill_seconds=10, absolute_backstop_seconds=50, now=clock
    )
    wd.note_activity(tool_started=True)
    clock.advance(60)  # tool wedged, but backstop exceeded
    reason = wd.overdue()
    assert reason is not None and "backstop" in reason


def test_hard_cap_bounds_even_an_active_turn() -> None:
    """An explicit caller hard cap (cron per-job budget) bounds the turn even if
    it keeps emitting events."""
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=600, hard_cap_seconds=30, now=clock)
    for _ in range(5):
        clock.advance(5)
        wd.note_activity()
        assert wd.overdue() is None
    clock.advance(10)  # 35s elapsed ≥ 30 hard cap
    reason = wd.overdue()
    assert reason is not None and "hard cap" in reason


def test_idle_disabled_never_fires_on_idle() -> None:
    clock = _Clock()
    wd = LivenessWatchdog(idle_kill_seconds=0, now=clock)
    clock.advance(1_000_000)
    assert wd.overdue() is None


def test_precedence_hardcap_then_backstop_then_idle() -> None:
    clock = _Clock()
    wd = LivenessWatchdog(
        idle_kill_seconds=10,
        absolute_backstop_seconds=20,
        hard_cap_seconds=15,
        now=clock,
    )
    clock.advance(16)  # exceeds hard cap (15) and idle (10), not backstop (20)
    assert "hard cap" in (wd.overdue() or "")


def test_seconds_until_due() -> None:
    clock = _Clock()
    # Idle armed, no tool, no cap → time until idle deadline.
    wd = LivenessWatchdog(idle_kill_seconds=10, now=clock)
    assert wd.seconds_until_due() == 10
    clock.advance(4)
    assert wd.seconds_until_due() == 6

    # Tool in flight + no cap/backstop → nothing armed → inf.
    wd2 = LivenessWatchdog(idle_kill_seconds=10, now=clock)
    wd2.note_activity(tool_started=True)
    assert wd2.seconds_until_due() == float("inf")

    # Idle disabled + no cap → inf.
    wd3 = LivenessWatchdog(idle_kill_seconds=0, now=clock)
    assert wd3.seconds_until_due() == float("inf")

    # Backstop is the soonest of the armed conditions.
    wd4 = LivenessWatchdog(
        idle_kill_seconds=100, absolute_backstop_seconds=30, now=clock
    )
    assert wd4.seconds_until_due() == 30

    # Never returns negative once a deadline has passed.
    clock.advance(1000)
    assert wd4.seconds_until_due() == 0.0
