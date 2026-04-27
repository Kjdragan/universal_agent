"""Tests for the /api/v1/dashboard/agent-metrics endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from universal_agent import gateway_server, task_hub


def _seed_completed_assignment(
    conn,
    *,
    agent_id: str = "research-specialist",
    started_at: str | None = None,
    ended_at: str | None = None,
    state: str = "completed",
) -> str:
    """Insert a single assignment row and return its assignment_id."""
    import uuid

    aid = f"assign-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state, started_at, ended_at, result_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            aid,
            f"task-{uuid.uuid4().hex[:8]}",
            agent_id,
            state,
            started_at or datetime.now(timezone.utc).isoformat(),
            ended_at or datetime.now(timezone.utc).isoformat(),
            "done",
        ),
    )
    conn.commit()
    return aid


def _seed_evaluation(conn, *, decision: str, evaluated_at: str | None = None):
    """Insert a single evaluation row."""
    import uuid

    conn.execute(
        "INSERT INTO task_hub_evaluations (id, task_id, evaluated_at, agent_id, decision, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            f"eval-{uuid.uuid4().hex[:8]}",
            f"task-{uuid.uuid4().hex[:8]}",
            evaluated_at or datetime.now(timezone.utc).isoformat(),
            "judge",
            decision,
            "test",
        ),
    )
    conn.commit()


@pytest.mark.asyncio
async def test_agent_metrics_empty_database(monkeypatch, tmp_path):
    """Endpoint returns zero-valued metrics when database has no data."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    response = await gateway_server.dashboard_agent_metrics()

    assert response["status"] == "ok"
    assert "generated_at" in response
    metrics = response["metrics"]
    assert metrics["avg_completion_time_seconds"] == {"1h": 0.0, "24h": 0.0, "7d": 0.0}
    assert metrics["success_rate_per_agent"] == {}
    assert metrics["routing_accuracy"] == {"seized": 0, "rejected": 0, "accuracy": 0.0}
    assert metrics["total_tasks_completed_7d"] == 0


@pytest.mark.asyncio
async def test_agent_metrics_avg_completion_time(monkeypatch, tmp_path):
    """Average completion time is computed correctly for completed assignments."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    now = datetime.now(timezone.utc)
    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            # Two completed assignments: 30s and 60s durations -> avg 45s
            _seed_completed_assignment(
                conn,
                started_at=(now - timedelta(seconds=60)).isoformat(),
                ended_at=now.isoformat(),
            )
            _seed_completed_assignment(
                conn,
                started_at=(now - timedelta(seconds=30)).isoformat(),
                ended_at=now.isoformat(),
            )
            # One assignment outside the 1h window (2 hours ago) should not count for 1h
            _seed_completed_assignment(
                conn,
                started_at=(now - timedelta(hours=2, seconds=120)).isoformat(),
                ended_at=(now - timedelta(hours=2)).isoformat(),
            )
        finally:
            conn.close()

    response = await gateway_server.dashboard_agent_metrics()
    assert response["status"] == "ok"
    metrics = response["metrics"]

    # 1h window should have exactly 2 assignments: avg 45.0
    assert metrics["avg_completion_time_seconds"]["1h"] == 45.0
    # 24h and 7d windows should include all 3: (60 + 30 + 120) / 3 = 70.0
    assert metrics["avg_completion_time_seconds"]["24h"] == 70.0
    assert metrics["avg_completion_time_seconds"]["7d"] == 70.0


@pytest.mark.asyncio
async def test_agent_metrics_success_rate_per_agent(monkeypatch, tmp_path):
    """Success rate is computed per agent based on completed vs total assignments."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            # Agent A: 3 completed, 1 failed -> rate 0.75
            _seed_completed_assignment(conn, agent_id="agent-a", state="completed")
            _seed_completed_assignment(conn, agent_id="agent-a", state="completed")
            _seed_completed_assignment(conn, agent_id="agent-a", state="completed")
            _seed_completed_assignment(conn, agent_id="agent-a", state="failed")
            # Agent B: 1 completed, 1 total -> rate 1.0
            _seed_completed_assignment(conn, agent_id="agent-b", state="completed")
        finally:
            conn.close()

    response = await gateway_server.dashboard_agent_metrics()
    assert response["status"] == "ok"
    rates = response["metrics"]["success_rate_per_agent"]

    assert rates["agent-a"]["completed"] == 3
    assert rates["agent-a"]["total"] == 4
    assert rates["agent-a"]["rate"] == 0.75

    assert rates["agent-b"]["completed"] == 1
    assert rates["agent-b"]["total"] == 1
    assert rates["agent-b"]["rate"] == 1.0


@pytest.mark.asyncio
async def test_agent_metrics_routing_accuracy(monkeypatch, tmp_path):
    """Routing accuracy is computed from seize/reject evaluations."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            for _ in range(9):
                _seed_evaluation(conn, decision="seize")
            for _ in range(1):
                _seed_evaluation(conn, decision="reject")
            # An evaluation with a different decision (e.g. "block") should not count
            _seed_evaluation(conn, decision="block")
        finally:
            conn.close()

    response = await gateway_server.dashboard_agent_metrics()
    assert response["status"] == "ok"
    ra = response["metrics"]["routing_accuracy"]

    assert ra["seized"] == 9
    assert ra["rejected"] == 1
    assert ra["accuracy"] == 0.9


@pytest.mark.asyncio
async def test_agent_metrics_total_completed_7d(monkeypatch, tmp_path):
    """total_tasks_completed_7d counts all completed assignments in the 7d window."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            _seed_completed_assignment(conn, state="completed")
            _seed_completed_assignment(conn, state="completed")
            _seed_completed_assignment(conn, state="completed")
            _seed_completed_assignment(conn, state="failed")
        finally:
            conn.close()

    response = await gateway_server.dashboard_agent_metrics()
    assert response["status"] == "ok"
    assert response["metrics"]["total_tasks_completed_7d"] == 3


@pytest.mark.asyncio
async def test_agent_metrics_excludes_stale_assignments(monkeypatch, tmp_path):
    """Assignments older than 7 days are excluded from all metrics."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    now = datetime.now(timezone.utc)
    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            # This assignment is 8 days old -- should be excluded
            _seed_completed_assignment(
                conn,
                started_at=(now - timedelta(days=8, seconds=60)).isoformat(),
                ended_at=(now - timedelta(days=8)).isoformat(),
                state="completed",
            )
            # This evaluation is 8 days old -- should be excluded
            _seed_evaluation(
                conn,
                decision="seize",
                evaluated_at=(now - timedelta(days=8)).isoformat(),
            )
        finally:
            conn.close()

    response = await gateway_server.dashboard_agent_metrics()
    assert response["status"] == "ok"
    metrics = response["metrics"]

    assert metrics["avg_completion_time_seconds"]["7d"] == 0.0
    assert metrics["success_rate_per_agent"] == {}
    assert metrics["routing_accuracy"] == {"seized": 0, "rejected": 0, "accuracy": 0.0}
    assert metrics["total_tasks_completed_7d"] == 0


@pytest.mark.asyncio
async def test_agent_metrics_response_shape(monkeypatch, tmp_path):
    """Verify the full response shape matches the expected schema."""
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    response = await gateway_server.dashboard_agent_metrics()

    assert response["status"] == "ok"
    assert isinstance(response["generated_at"], str)

    metrics = response["metrics"]
    assert "avg_completion_time_seconds" in metrics
    assert "1h" in metrics["avg_completion_time_seconds"]
    assert "24h" in metrics["avg_completion_time_seconds"]
    assert "7d" in metrics["avg_completion_time_seconds"]

    assert isinstance(metrics["success_rate_per_agent"], dict)

    assert "seized" in metrics["routing_accuracy"]
    assert "rejected" in metrics["routing_accuracy"]
    assert "accuracy" in metrics["routing_accuracy"]

    assert isinstance(metrics["total_tasks_completed_7d"], int)


def test_parse_iso_utc_valid():
    assert gateway_server._parse_iso_utc("2026-03-26T12:00:00Z") is not None
    assert gateway_server._parse_iso_utc("2026-03-26T12:00:00+00:00") is not None
    assert gateway_server._parse_iso_utc("2026-03-26T12:00:00") is not None  # no tz -> assumes UTC


def test_parse_iso_utc_invalid():
    assert gateway_server._parse_iso_utc("") is None
    assert gateway_server._parse_iso_utc(None) is None
    assert gateway_server._parse_iso_utc("not-a-date") is None
