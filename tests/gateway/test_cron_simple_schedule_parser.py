import pytest

from universal_agent.gateway_server import (
    _normalize_interval_from_text,
    _resolve_simplified_schedule_fields,
)


def test_normalize_interval_from_text():
    assert _normalize_interval_from_text("in 30 minutes") == "30m"
    assert _normalize_interval_from_text("15m") == "15m"
    assert _normalize_interval_from_text("2 hours") == "2h"


def test_resolve_simplified_schedule_one_shot():
    every, cron_expr, run_at, delete_after_run = _resolve_simplified_schedule_fields(
        schedule_time="in 20 minutes",
        repeat=False,
        timezone_name="UTC",
    )
    assert every is None
    assert cron_expr is None
    assert run_at is not None
    assert delete_after_run is True


def test_resolve_simplified_schedule_repeat_interval():
    every, cron_expr, run_at, delete_after_run = _resolve_simplified_schedule_fields(
        schedule_time="in 15 minutes",
        repeat=True,
        timezone_name="UTC",
    )
    assert every == "15m"
    assert cron_expr is None
    assert run_at is None
    assert delete_after_run is False


def test_resolve_simplified_schedule_repeat_daily_clock_time():
    every, cron_expr, run_at, delete_after_run = _resolve_simplified_schedule_fields(
        schedule_time="4:30 pm",
        repeat=True,
        timezone_name="America/Chicago",
    )
    assert every is None
    assert cron_expr == "30 16 * * *"
    assert run_at is None
    assert delete_after_run is False


def test_resolve_simplified_schedule_repeat_invalid():
    with pytest.raises(ValueError):
        _resolve_simplified_schedule_fields(
            schedule_time="tomorrow evening sometime",
            repeat=True,
            timezone_name="UTC",
        )
