import pytest
from fastapi import HTTPException

import universal_agent.gateway_server as gateway_server
from universal_agent.gateway_server import (
    _AgentScheduleInterpretation,
    _normalize_interval_from_text,
    _resolve_simplified_schedule_fields,
    _resolve_simplified_schedule_update_fields_with_agent,
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


@pytest.mark.asyncio
async def test_schedule_update_uses_agent_interpretation(monkeypatch):
    async def _fake_interpret(**kwargs):
        return _AgentScheduleInterpretation(
            status="ok",
            every="every 45 minutes",
            cron_expr=None,
            run_at=None,
            delete_after_run=False,
            reason="parsed as repeating interval",
        )

    monkeypatch.setattr(
        gateway_server,
        "_interpret_schedule_with_system_configuration_agent",
        _fake_interpret,
    )
    job = type(
        "Job",
        (),
        {"job_id": "abc", "every_seconds": 0, "cron_expr": None, "run_at": None, "timezone": "UTC"},
    )()
    every, cron_expr, run_at, delete_after_run = await _resolve_simplified_schedule_update_fields_with_agent(
        schedule_time="every 45 minutes",
        repeat=None,
        timezone_name="UTC",
        job=job,
    )
    assert every == "45m"
    assert cron_expr is None
    assert run_at is None
    assert delete_after_run is False


@pytest.mark.asyncio
async def test_schedule_update_agent_fallback_to_deterministic_parser(monkeypatch):
    async def _fake_interpret(**kwargs):
        raise RuntimeError("agent unavailable")

    monkeypatch.setattr(
        gateway_server,
        "_interpret_schedule_with_system_configuration_agent",
        _fake_interpret,
    )
    job = type(
        "Job",
        (),
        {"job_id": "abc", "every_seconds": 0, "cron_expr": None, "run_at": None, "timezone": "UTC"},
    )()
    every, cron_expr, run_at, delete_after_run = await _resolve_simplified_schedule_update_fields_with_agent(
        schedule_time="in 15 minutes",
        repeat=True,
        timezone_name="UTC",
        job=job,
    )
    assert every == "15m"
    assert cron_expr is None
    assert run_at is None
    assert delete_after_run is False


@pytest.mark.asyncio
async def test_schedule_update_agent_clarification_bubbles_up(monkeypatch):
    async def _fake_interpret(**kwargs):
        return _AgentScheduleInterpretation(
            status="needs_clarification",
            reason="Specify if this should repeat or run once.",
        )

    monkeypatch.setattr(
        gateway_server,
        "_interpret_schedule_with_system_configuration_agent",
        _fake_interpret,
    )
    job = type(
        "Job",
        (),
        {"job_id": "abc", "every_seconds": 0, "cron_expr": None, "run_at": None, "timezone": "UTC"},
    )()
    with pytest.raises(HTTPException) as exc:
        await _resolve_simplified_schedule_update_fields_with_agent(
            schedule_time="next tuesday at noon",
            repeat=None,
            timezone_name="UTC",
            job=job,
        )
    assert exc.value.status_code == 400
    assert "Specify if this should repeat or run once." in str(exc.value.detail)
