from __future__ import annotations

from pathlib import Path


_PAGE = Path("web-ui/app/dashboard/todolist/page.tsx")


def test_todolist_dashboard_uses_gateway_api_path():
    content = _PAGE.read_text(encoding="utf-8")
    assert 'const API_BASE = "/api/dashboard/gateway";' in content
    assert "ENDPOINTS.pipeline" in content
    assert "ENDPOINTS.actionable" in content
    assert "ENDPOINTS.heartbeat" in content


def test_todolist_dashboard_includes_api_diagnostics_and_mismatch_warning():
    content = _PAGE.read_text(encoding="utf-8")
    assert "API Diagnostics" in content
    assert "Pipeline task count is" in content
    assert "@agent-ready" in content
