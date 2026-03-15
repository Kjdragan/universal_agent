from __future__ import annotations

from pathlib import Path


_PAGE = Path("web-ui/app/dashboard/todolist/page.tsx")


def test_todolist_dashboard_uses_gateway_api_path():
    content = _PAGE.read_text(encoding="utf-8")
    assert 'const API_BASE = "/api/dashboard/gateway";' in content
    assert "/api/v1/dashboard/todolist/overview" in content
    assert "/api/v1/dashboard/todolist/agent-queue" in content
    assert "/api/v1/dashboard/todolist/completed" in content


def test_todolist_dashboard_includes_history_and_completed_sections():
    content = _PAGE.read_text(encoding="utf-8")
    assert "Task History" in content
    assert "/api/v1/dashboard/todolist/tasks/" in content
    assert "/history?limit=120" in content


def test_todolist_dashboard_includes_heartbeat_force_controls():
    content = _PAGE.read_text(encoding="utf-8")
    assert "Run Heartbeat" in content
    assert "/api/v1/heartbeat/wake" in content


def test_todolist_dashboard_mission_focused_layout():
    content = _PAGE.read_text(encoding="utf-8")
    # New mission-focused elements
    assert "Task Command Center" in content
    assert "Dispatch Eligible" in content
    assert "Active Agents" in content
    assert "Completion Rate" in content
    assert "source_kind" in content
    # Kanban time horizons
    assert "Future" in content
    assert "In Progress" in content
    assert "Past" in content
    # Allocation breakdown
    assert "Work Allocation" in content
    # No CSI-specific content
    assert "Open CSI Incidents" not in content
    assert "Human Intervention Required" not in content
    assert "CSI Escalation" not in content
    assert "/dashboard/csi#notifications" not in content
