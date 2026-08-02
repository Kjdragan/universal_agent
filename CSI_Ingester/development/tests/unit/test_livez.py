"""Pure-logic tests for csi_ingester.livez (T13).

These exercise `compute_livez_status` and its helpers directly — no FastAPI,
no adapter, no DB — matching the module's design goal of being testable in
isolation. Note: `tests/unit` at the *repo root* (the one PR-Validate's
`pytest tests/unit` step actually runs) does not execute this directory —
CSI_Ingester/development/tests is a separate pytest project. See the T13 PR
description for the CI-coverage gap this implies (pre-existing, not
introduced by this change).
"""

from __future__ import annotations

from datetime import datetime, timezone

from csi_ingester.livez import (
    DEFAULT_MIN_GRACE_SECONDS,
    compute_grace_seconds,
    compute_largest_gap_hours,
    compute_livez_status,
    is_quiet_window,
)

HOUR = 3600.0
CT_SCHEDULE = [2, 6, 8, 10, 12, 14, 16, 18, 20]  # production config/config.yaml


def test_largest_gap_hours_typical_schedule():
    # 20:00 -> 02:00 wraps to a 6h gap; every other consecutive pair is 2h.
    assert compute_largest_gap_hours(CT_SCHEDULE) == 6.0


def test_largest_gap_hours_empty_schedule_is_full_day():
    assert compute_largest_gap_hours([]) == 24.0


def test_largest_gap_hours_single_hour_is_full_day():
    assert compute_largest_gap_hours([9]) == 24.0


def test_largest_gap_hours_dedupes_and_normalizes_mod_24():
    # Duplicate and out-of-range (25 -> 1) hours must not distort the gap.
    assert compute_largest_gap_hours([1, 1, 25, 13]) == 12.0


def test_grace_seconds_derived_from_schedule_default_multiplier():
    grace = compute_grace_seconds(schedule_fetch_hours=CT_SCHEDULE)
    assert grace == 6.0 * HOUR * 2.0  # 12h


def test_grace_seconds_floor_applies_for_tiny_poll_interval():
    # No hour-of-day schedule and a very small poll interval would derive an
    # unreasonably small (flap-prone) grace without the floor.
    grace = compute_grace_seconds(schedule_fetch_hours=[], poll_interval_seconds=30.0)
    assert grace == DEFAULT_MIN_GRACE_SECONDS


def test_grace_seconds_no_schedule_uses_poll_interval():
    grace = compute_grace_seconds(schedule_fetch_hours=[], poll_interval_seconds=1800.0)
    assert grace == 1800.0 * 2.0


def test_is_quiet_window_true_outside_schedule():
    now = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)  # hour 3, not in CT_SCHEDULE
    assert is_quiet_window(schedule_fetch_hours=CT_SCHEDULE, now=now) is True


def test_is_quiet_window_false_inside_schedule():
    now = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)  # hour 8, in CT_SCHEDULE
    assert is_quiet_window(schedule_fetch_hours=CT_SCHEDULE, now=now) is False


def test_is_quiet_window_false_when_no_schedule_configured():
    now = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
    assert is_quiet_window(schedule_fetch_hours=[], now=now) is False


# ── compute_livez_status: the ticket's required cases ──────────────────────


def test_livez_healthy_inside_quiet_window_even_with_stale_epoch():
    """The T13 false-positive: hour 3 CT is excluded from CT_SCHEDULE, so a
    fetch that hasn't happened in days is expected there, not a failure."""
    now_epoch = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc).timestamp()
    stale_epoch = now_epoch - 5 * 24 * HOUR  # 5 days stale
    status = compute_livez_status(
        schedule_fetch_hours=CT_SCHEDULE,
        schedule_timezone="UTC",
        last_fetch_epoch=stale_epoch,
        now_epoch=now_epoch,
    )
    assert status.healthy is True
    assert status.reason == "quiet_window"
    assert status.in_quiet_window is True


def test_livez_unhealthy_when_overdue_outside_quiet_window():
    """Hour 8 CT IS a scheduled fetch hour; a fetch 20h stale badly exceeds
    the 12h grace derived from the schedule -> genuinely overdue."""
    now_epoch = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc).timestamp()
    stale_epoch = now_epoch - 20 * HOUR
    status = compute_livez_status(
        schedule_fetch_hours=CT_SCHEDULE,
        schedule_timezone="UTC",
        last_fetch_epoch=stale_epoch,
        now_epoch=now_epoch,
    )
    assert status.healthy is False
    assert status.reason == "fetch_overdue"
    assert status.in_quiet_window is False
    assert status.seconds_since_fetch == 20 * HOUR


def test_livez_healthy_within_grace_outside_quiet_window():
    now_epoch = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc).timestamp()
    recent_epoch = now_epoch - 1 * HOUR  # well inside the 12h grace
    status = compute_livez_status(
        schedule_fetch_hours=CT_SCHEDULE,
        schedule_timezone="UTC",
        last_fetch_epoch=recent_epoch,
        now_epoch=now_epoch,
    )
    assert status.healthy is True
    assert status.reason == "within_grace"
    assert status.in_quiet_window is False


def test_livez_healthy_when_never_fetched_no_baseline():
    """epoch<=0 (fresh deploy / not-yet-persisted) — insufficient evidence to
    call it overdue; the scheduler's immediate first tick resolves this
    within seconds on a healthy process."""
    now_epoch = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc).timestamp()
    status = compute_livez_status(
        schedule_fetch_hours=CT_SCHEDULE,
        schedule_timezone="UTC",
        last_fetch_epoch=0.0,
        now_epoch=now_epoch,
    )
    assert status.healthy is True
    assert status.reason == "no_fetch_observed_yet"
    assert status.seconds_since_fetch is None


def test_livez_no_schedule_configured_within_grace_is_healthy():
    now_epoch = 10_000_000.0
    status = compute_livez_status(
        schedule_fetch_hours=[],
        poll_interval_seconds=60.0,
        last_fetch_epoch=now_epoch - 30.0,
        now_epoch=now_epoch,
    )
    assert status.healthy is True
    assert status.in_quiet_window is False


def test_livez_no_schedule_configured_overdue_is_unhealthy():
    now_epoch = 10_000_000.0
    status = compute_livez_status(
        schedule_fetch_hours=[],
        poll_interval_seconds=60.0,
        # grace floors at DEFAULT_MIN_GRACE_SECONDS (1800s) here since
        # 2x60s would otherwise be an unreasonably tight window — go well
        # past the floor to land solidly in "overdue".
        last_fetch_epoch=now_epoch - (DEFAULT_MIN_GRACE_SECONDS + 500.0),
        now_epoch=now_epoch,
    )
    assert status.healthy is False
    assert status.reason == "fetch_overdue"
