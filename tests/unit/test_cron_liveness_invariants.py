"""Tests for the cron scheduler liveness invariants (top-9 handoff, task 5).

``cron_loop_liveness``: an enabled cron whose next_run_at is in the past
beyond a cadence-scaled grace means the scheduler loop is wedged.
``cron_tick_fired``: a seeded run-row-writing cron whose tick passed must
have a task_hub_runs row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import sqlite3

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_registry_for_tests()
    from universal_agent.services.invariants import cron_liveness

    importlib.reload(cron_liveness)
    yield
    clear_registry_for_tests()


def _job(
    *,
    job_id: str = "j1",
    system_job: str = "",
    cron_expr: str = "*/1 * * * *",
    next_run_delta_s: float = 60.0,
    enabled: bool = True,
    tz: str = "UTC",
) -> dict:
    return {
        "job_id": job_id,
        "enabled": enabled,
        "cron_expr": cron_expr,
        "timezone": tz,
        "next_run_at": (
            datetime.now(timezone.utc) + timedelta(seconds=next_run_delta_s)
        ).isoformat(),
        "metadata": {"system_job": system_job} if system_job else {},
    }


def _findings(ctx, metric_key):
    return [f for f in run_invariants(ctx) if f.metric_key == metric_key]


def test_both_register_on_import():
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert "cron_loop_liveness" in ids
    assert "cron_tick_fired" in ids


# ── cron_loop_liveness ──────────────────────────────────────────────────────


def test_loop_liveness_quiet_when_next_run_in_future():
    ctx = {"cron_jobs": [_job(next_run_delta_s=120)]}
    assert _findings(ctx, "cron_loop_liveness") == []


def test_loop_liveness_fires_critical_on_stale_next_run():
    """Every-minute cron overdue >3 min (floor) → the scheduler is wedged."""
    ctx = {"cron_jobs": [_job(system_job="simone_chat_auto_complete", next_run_delta_s=-600)]}
    found = _findings(ctx, "cron_loop_liveness")
    assert len(found) == 1
    assert found[0].severity == "critical"
    overdue = found[0].observed_value["overdue_jobs"]
    assert overdue[0]["system_job"] == "simone_chat_auto_complete"
    assert overdue[0]["overdue_minutes"] >= 9.9


def test_loop_liveness_daily_cron_caught_within_two_hours():
    """A daily cron 3h overdue must fire — the 2h cap beats cron_staleness's
    48h latency for sparse schedules (the whole point of this invariant)."""
    ctx = {
        "cron_jobs": [
            _job(
                job_id="daily",
                system_job="paper_to_podcast_daily",
                cron_expr="0 21 * * *",
                next_run_delta_s=-3 * 3600,
            )
        ]
    }
    found = _findings(ctx, "cron_loop_liveness")
    assert len(found) == 1
    assert found[0].observed_value["overdue_jobs"][0]["system_job"] == "paper_to_podcast_daily"


def test_loop_liveness_grace_absorbs_minor_lag():
    """2 min late on an every-minute cron is lag, not a wedge (floor 3 min)."""
    ctx = {"cron_jobs": [_job(next_run_delta_s=-120)]}
    assert _findings(ctx, "cron_loop_liveness") == []


def test_loop_liveness_daily_grace_absorbs_long_run():
    """A daily job that dispatched and is still mid-run (next_run_at 1h old)
    stays quiet under the 2h cap."""
    ctx = {"cron_jobs": [_job(cron_expr="0 21 * * *", next_run_delta_s=-3600)]}
    assert _findings(ctx, "cron_loop_liveness") == []


def test_loop_liveness_ignores_disabled_jobs():
    ctx = {"cron_jobs": [_job(enabled=False, next_run_delta_s=-864000)]}
    assert _findings(ctx, "cron_loop_liveness") == []


def test_loop_liveness_epoch_next_run_supported():
    """Live cron_jobs.json stores next_run_at as an epoch float."""
    job = _job(system_job="x")
    job["next_run_at"] = (datetime.now(timezone.utc) - timedelta(hours=4)).timestamp()
    job["cron_expr"] = "0 21 * * *"
    found = _findings({"cron_jobs": [job]}, "cron_loop_liveness")
    assert len(found) == 1


# ── cron_tick_fired ─────────────────────────────────────────────────────────


def _runs_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE task_hub_runs ("
        "run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, started_at TEXT NOT NULL)"
    )
    return conn


def _seed_run(conn, task_id: str, started_at: datetime) -> None:
    conn.execute(
        "INSERT INTO task_hub_runs (run_id, task_id, started_at) VALUES (?, ?, ?)",
        (f"r{started_at.timestamp()}", task_id, started_at.isoformat()),
    )
    conn.commit()


def _hourly_seed_ctx(conn, *, monkeypatch, seeds="hourly_job"):
    """A seed job whose tick fires at :00 every hour — the previous tick is
    at most 60 min old, so with the 30-min grace the tick is judgeable once
    now is >30 min past the hour. To keep the test deterministic we use an
    every-minute expr instead and rely on grace=0 semantics via env."""
    monkeypatch.setenv("UA_CRON_TICK_FIRED_JOBS", seeds)
    return {
        "activity_conn": conn,
        "cron_jobs": [
            {
                "job_id": "h1",
                "enabled": True,
                "cron_expr": "*/1 * * * *",
                "timezone": "UTC",
                "next_run_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {"system_job": seeds},
            }
        ],
    }


def test_tick_fired_breach_when_no_run_row(monkeypatch):
    """Tick long past + grace elapsed + zero run rows → critical."""
    monkeypatch.setenv("UA_CRON_TICK_FIRED_GRACE_MINUTES", "0")
    from universal_agent.services.invariants import cron_liveness

    importlib.reload(cron_liveness)
    conn = _runs_conn()
    ctx = _hourly_seed_ctx(conn, monkeypatch=monkeypatch)
    found = _findings(ctx, "cron_tick_fired")
    assert len(found) == 1
    assert found[0].severity == "critical"
    missing = found[0].observed_value["missing_ticks"]
    assert missing[0]["system_job"] == "hourly_job"
    assert missing[0]["latest_run_started_at"] is None


def test_tick_fired_quiet_when_run_row_exists(monkeypatch):
    monkeypatch.setenv("UA_CRON_TICK_FIRED_GRACE_MINUTES", "0")
    from universal_agent.services.invariants import cron_liveness

    importlib.reload(cron_liveness)
    conn = _runs_conn()
    _seed_run(conn, "cron:hourly_job", datetime.now(timezone.utc))
    ctx = _hourly_seed_ctx(conn, monkeypatch=monkeypatch)
    assert _findings(ctx, "cron_tick_fired") == []


def test_tick_fired_stale_run_row_still_breaches(monkeypatch):
    """A run row from yesterday does not vouch for today's tick."""
    monkeypatch.setenv("UA_CRON_TICK_FIRED_GRACE_MINUTES", "0")
    from universal_agent.services.invariants import cron_liveness

    importlib.reload(cron_liveness)
    conn = _runs_conn()
    _seed_run(
        conn, "cron:hourly_job", datetime.now(timezone.utc) - timedelta(days=1)
    )
    ctx = _hourly_seed_ctx(conn, monkeypatch=monkeypatch)
    found = _findings(ctx, "cron_tick_fired")
    assert len(found) == 1
    assert found[0].observed_value["missing_ticks"][0]["latest_run_started_at"]


def test_tick_fired_within_grace_stays_quiet(monkeypatch):
    """Tick just passed, grace not yet elapsed → too soon to judge."""
    monkeypatch.setenv("UA_CRON_TICK_FIRED_GRACE_MINUTES", "90")
    from universal_agent.services.invariants import cron_liveness

    importlib.reload(cron_liveness)
    conn = _runs_conn()  # no rows at all
    ctx = _hourly_seed_ctx(conn, monkeypatch=monkeypatch)
    assert _findings(ctx, "cron_tick_fired") == []


def test_tick_fired_skips_disabled_and_absent_seeds(monkeypatch):
    """Operator-disabled or unregistered seeds are skipped, not alarmed."""
    monkeypatch.setenv("UA_CRON_TICK_FIRED_GRACE_MINUTES", "0")
    monkeypatch.setenv("UA_CRON_TICK_FIRED_JOBS", "disabled_job,ghost_job")
    from universal_agent.services.invariants import cron_liveness

    importlib.reload(cron_liveness)
    conn = _runs_conn()
    ctx = {
        "activity_conn": conn,
        "cron_jobs": [
            {
                "job_id": "d1",
                "enabled": False,
                "cron_expr": "*/1 * * * *",
                "timezone": "UTC",
                "metadata": {"system_job": "disabled_job"},
            }
        ],
    }
    assert _findings(ctx, "cron_tick_fired") == []


def test_tick_fired_fail_open_without_conn(monkeypatch):
    monkeypatch.setenv("UA_CRON_TICK_FIRED_GRACE_MINUTES", "0")
    from universal_agent.services.invariants import cron_liveness

    importlib.reload(cron_liveness)
    ctx = {"cron_jobs": [_job(system_job="paper_to_podcast_daily")]}
    assert _findings(ctx, "cron_tick_fired") == []
