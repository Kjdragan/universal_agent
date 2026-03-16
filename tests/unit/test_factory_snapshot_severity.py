"""Tests for build_factory_snapshot severity — LOCAL_WORKER offline handling."""
from __future__ import annotations

from universal_agent.supervisors.builders import build_factory_snapshot


def _empty_todolist_overview() -> dict:
    return {
        "queue_health": {
            "dispatch_eligible": 0,
            "status_counts": {},
            "source_counts": {},
        },
        "csi_incident_summary": {"open_incidents": 0},
        "agent_activity": {"backlog_open": 0, "active_agents": 0},
        "heartbeat": {},
    }


def _make_registration(factory_role: str, status: str) -> dict:
    return {"factory_role": factory_role, "registration_status": status}


def test_offline_local_worker_produces_warning_not_critical():
    """An offline LOCAL_WORKER should downgrade severity to warning, not critical."""
    result = build_factory_snapshot(
        capabilities={"factory_role": "HEADQUARTERS"},
        registrations=[
            _make_registration("HEADQUARTERS", "active"),
            _make_registration("LOCAL_WORKER", "offline"),
        ],
        delegation_history=[],
        todolist_overview=_empty_todolist_overview(),
        agent_queue=[],
        dispatch_queue=[],
        events=[],
        timers=[],
    )
    assert result["severity"] == "warning", (
        f"Expected 'warning' for offline LOCAL_WORKER, got '{result['severity']}'"
    )


def test_offline_fleet_worker_produces_critical():
    """An offline non-LOCAL_WORKER (e.g., WORKER) should stay critical."""
    result = build_factory_snapshot(
        capabilities={"factory_role": "HEADQUARTERS"},
        registrations=[
            _make_registration("HEADQUARTERS", "active"),
            _make_registration("WORKER", "offline"),
        ],
        delegation_history=[],
        todolist_overview=_empty_todolist_overview(),
        agent_queue=[],
        dispatch_queue=[],
        events=[],
        timers=[],
    )
    assert result["severity"] == "critical", (
        f"Expected 'critical' for offline WORKER, got '{result['severity']}'"
    )


def test_all_active_produces_info():
    """When all factories are active and nothing triggers, severity should be info."""
    result = build_factory_snapshot(
        capabilities={"factory_role": "HEADQUARTERS"},
        registrations=[
            _make_registration("HEADQUARTERS", "active"),
            _make_registration("LOCAL_WORKER", "active"),
        ],
        delegation_history=[],
        todolist_overview=_empty_todolist_overview(),
        agent_queue=[],
        dispatch_queue=[],
        events=[],
        timers=[],
    )
    assert result["severity"] == "info", (
        f"Expected 'info' for all active, got '{result['severity']}'"
    )
