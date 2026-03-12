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
    assert "Completed Agent Jobs" in content
    assert "Task History" in content
    assert "/api/v1/dashboard/todolist/tasks/" in content
    assert "/history?limit=120" in content


def test_todolist_dashboard_includes_heartbeat_force_controls():
    content = _PAGE.read_text(encoding="utf-8")
    assert "Run Next Heartbeat" in content
    assert "Force Next Heartbeat" in content
    assert "/api/v1/heartbeat/wake" in content


def test_todolist_dashboard_links_csi_incidents_and_uses_detailed_queue_cards():
    content = _PAGE.read_text(encoding="utf-8")
    assert '/dashboard/csi#notifications' in content
    assert "score {item.score ?? 0} · Q {item.score_confidence ?? 0}" in content
    assert "Human Intervention Required" in content
    assert "CSI Escalation" in content
    assert "Next Actions" not in content
