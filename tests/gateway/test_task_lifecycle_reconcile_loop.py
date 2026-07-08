"""Tests for the periodic task-lifecycle reconcile loop.

The loop is thin glue around ``task_hub.reconcile_task_lifecycle`` (already
covered by test_task_hub_pipeline_repair.py / test_cron_reconcile_grace.py);
what matters here is that the loop (a) invokes the reconciler with the SAFE
daemon-alive parameterization — live session ids snapshot, rebuild_queue=True,
NONZERO cron grace so young in-process cron runs are never false-orphaned —
and (b) exits promptly on the stop event so gateway shutdown can't hang.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from universal_agent import gateway_server


class _FakeStopEvent:
    """Deterministic stand-in for asyncio.Event: 'times out' N times, then stops.

    ``wait()`` raising asyncio.TimeoutError mimics the loop's
    ``asyncio.wait_for(stop_event.wait(), timeout=interval)`` expiring — no real
    sleeping, so the test doesn't wait out the 60s interval floor.
    """

    def __init__(self, timeouts_before_stop: int):
        self._remaining = timeouts_before_stop
        self._stopped = timeouts_before_stop <= 0

    def is_set(self) -> bool:
        return self._stopped

    async def wait(self):
        if self._remaining > 0:
            self._remaining -= 1
            raise asyncio.TimeoutError
        self._stopped = True
        return True


@pytest.mark.asyncio
async def test_loop_sweeps_with_safe_daemon_alive_parameterization(monkeypatch):
    calls: list[dict] = []

    def _fake_reconcile(conn, *, running_session_ids, rebuild_queue, cron_live_grace_seconds):
        calls.append(
            {
                "running_session_ids": set(running_session_ids),
                "rebuild_queue": rebuild_queue,
                "cron_live_grace_seconds": cron_live_grace_seconds,
            }
        )
        return {"reopened": 1, "reviewed": 0}

    monkeypatch.setattr(gateway_server.task_hub, "reconcile_task_lifecycle", _fake_reconcile)
    monkeypatch.setattr(gateway_server.task_hub, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(gateway_server, "connect_runtime_db", lambda path: sqlite3.connect(":memory:"))
    monkeypatch.setattr(gateway_server, "_running_execution_session_ids", lambda: {"live-session-1"})

    # One timeout -> one sweep -> stop.
    await gateway_server._task_lifecycle_reconcile_loop(_FakeStopEvent(timeouts_before_stop=1))

    assert len(calls) == 1
    call = calls[0]
    # Live-session snapshot must reach the reconciler (protects gateway-hosted runs).
    assert call["running_session_ids"] == {"live-session-1"}
    # Reopened tasks must become claimable again.
    assert call["rebuild_queue"] is True
    # Daemon is alive during a periodic sweep: young cron runs must be protected.
    assert call["cron_live_grace_seconds"] > 0


@pytest.mark.asyncio
async def test_loop_exits_immediately_when_stopped(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("reconcile must not run when stop is already set")

    monkeypatch.setattr(gateway_server.task_hub, "reconcile_task_lifecycle", _boom)

    await asyncio.wait_for(
        gateway_server._task_lifecycle_reconcile_loop(_FakeStopEvent(timeouts_before_stop=0)),
        timeout=5.0,
    )


@pytest.mark.asyncio
async def test_loop_survives_a_failing_sweep(monkeypatch):
    """The janitor must never die on a sweep error — it logs and keeps looping."""
    attempts: list[int] = []

    def _failing_reconcile(conn, **kwargs):
        attempts.append(1)
        raise RuntimeError("transient DB error")

    monkeypatch.setattr(gateway_server.task_hub, "reconcile_task_lifecycle", _failing_reconcile)
    monkeypatch.setattr(gateway_server.task_hub, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(gateway_server, "connect_runtime_db", lambda path: sqlite3.connect(":memory:"))
    monkeypatch.setattr(gateway_server, "_running_execution_session_ids", lambda: set())

    # Two timeouts -> two sweep attempts despite the first raising -> stop.
    await gateway_server._task_lifecycle_reconcile_loop(_FakeStopEvent(timeouts_before_stop=2))

    assert len(attempts) == 2
