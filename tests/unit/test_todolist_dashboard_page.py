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
    assert "Work Item History" in content
    assert "/api/v1/dashboard/todolist/tasks/" in content
    assert "/history?limit=120" in content


def test_todolist_dashboard_includes_heartbeat_force_controls():
    content = _PAGE.read_text(encoding="utf-8")
    assert "Heartbeat" in content
    assert "/api/v1/heartbeat/wake" in content


def test_todolist_dashboard_mission_focused_layout():
    content = _PAGE.read_text(encoding="utf-8")
    # Core data binding identifiers (stable across UI redesigns)
    assert "Task Hub" in content
    assert "dispatch_eligible" in content
    assert "active_agents" in content
    assert "completionRate24h" in content
    assert "Dispatcher" in content
    assert "last_result_state" in content
    assert "source_kind" in content
    # Kanban board lanes
    assert "Not Assigned" in content
    assert "In Progress" in content
    assert "Needs Review" in content
    assert "Completed" in content
    # Allocation breakdown
    assert "Work Allocation" in content
    assert "Reopened" in content
    assert "retryable after failed run" in content
    # No CSI-specific content
    assert "Open CSI Incidents" not in content
    assert "Human Intervention Required" not in content
    assert "CSI Escalation" not in content
    assert "/dashboard/csi#notifications" not in content


def test_todolist_session_button_surfaces_inline():
    """The Session button should fetch session details inline, not navigate to the Sessions tab."""
    content = _PAGE.read_text(encoding="utf-8")
    # Should use the ops session detail API for inline fetching
    assert "api/v1/ops/sessions/" in content
    assert "handleOpenSession" in content
    assert "selectedSessionDetail" in content
    assert "renderSessionDetailModal" in content
    # Should include a fallback link to open full sessions tab
    assert "Open in Sessions Tab" in content
