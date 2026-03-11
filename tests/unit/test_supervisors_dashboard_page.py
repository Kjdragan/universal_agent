from __future__ import annotations

from pathlib import Path


_PAGE = Path("web-ui/app/dashboard/supervisors/page.tsx")


def test_supervisors_dashboard_uses_gateway_api_path_and_endpoints():
    content = _PAGE.read_text(encoding="utf-8")
    assert 'const API_BASE = "/api/dashboard/gateway";' in content
    assert "/api/v1/dashboard/supervisors/registry" in content
    assert "/api/v1/dashboard/supervisors/${encodeURIComponent(selected)}/snapshot" in content
    assert "/api/v1/dashboard/supervisors/${encodeURIComponent(selected)}/run" in content


def test_supervisors_dashboard_contains_expected_labels_and_controls():
    content = _PAGE.read_text(encoding="utf-8")
    assert "Supervisor Agents" in content
    assert "Factory Supervisor" in content
    assert "CSI Supervisor" in content
    assert "Run now" in content
    assert "setInterval" in content
