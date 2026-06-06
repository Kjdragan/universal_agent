"""Tests for context-window pressure flagging (ISSUE #4).

These flags track live CONTEXT-WINDOW occupancy (~200K), NOT the mission token
budget. The keys are prefixed context_window_ so audits never misread them as
mission-budget alarms. The math here is deliberately denominator-agnostic — the
caller passes whatever window size applies.
"""

from universal_agent.main import update_context_window_pressure_flags


def test_below_70_records_no_warned_flags():
    flags = {}
    crossed = update_context_window_pressure_flags(flags, 100_000, 200_000)
    assert crossed == []
    assert "context_window_warned_70" not in flags
    assert "context_window_warned_90" not in flags
    # Denominator / occupancy fields are still recorded.
    assert flags["context_window_tokens"] == 200_000
    assert flags["context_window_used"] == 100_000
    assert flags["context_window_ratio"] == 0.5


def test_crossing_70_sets_flag_and_returns_label_once():
    flags = {}
    crossed = update_context_window_pressure_flags(flags, 140_000, 200_000)
    assert crossed == ["70"]
    assert flags["context_window_warned_70"] is True
    assert flags["context_window_ratio"] == 0.7

    # One-shot: a second call at the same level does not re-cross.
    crossed_again = update_context_window_pressure_flags(flags, 145_000, 200_000)
    assert crossed_again == []
    assert flags["context_window_warned_70"] is True


def test_crossing_90_sets_flag():
    flags = {}
    crossed = update_context_window_pressure_flags(flags, 190_000, 200_000)
    # Both thresholds cross on the first call from a clean state.
    assert "70" in crossed
    assert "90" in crossed
    assert flags["context_window_warned_70"] is True
    assert flags["context_window_warned_90"] is True
    assert flags["context_window_ratio"] == 0.95


def test_90_crossed_only_once_after_70_already_seen():
    flags = {}
    # First cross 70 only.
    first = update_context_window_pressure_flags(flags, 140_000, 200_000)
    assert first == ["70"]
    # Then cross 90 — 70 is already flagged, so only "90" is newly crossed.
    second = update_context_window_pressure_flags(flags, 185_000, 200_000)
    assert second == ["90"]
    assert flags["context_window_warned_90"] is True
    # Third call: nothing new.
    third = update_context_window_pressure_flags(flags, 195_000, 200_000)
    assert third == []


def test_ratio_and_denominator_fields_recorded():
    flags = {}
    update_context_window_pressure_flags(flags, 50_000, 200_000)
    assert flags["context_window_tokens"] == 200_000
    assert flags["context_window_used"] == 50_000
    assert flags["context_window_ratio"] == 0.25


def test_zero_window_returns_empty_and_records_nothing():
    flags = {}
    crossed = update_context_window_pressure_flags(flags, 100_000, 0)
    assert crossed == []
    # Guard short-circuits before recording any fields.
    assert flags == {}
