from __future__ import annotations

from pathlib import Path


_LAYOUT = Path("web-ui/app/dashboard/layout.tsx")


def test_dashboard_layout_includes_supervisors_nav_item_hq_only():
    content = _LAYOUT.read_text(encoding="utf-8")
    assert 'href: "/dashboard/supervisors"' in content
    assert 'label: "Supervisor Agents"' in content
    assert "requiresHeadquarters: true" in content
