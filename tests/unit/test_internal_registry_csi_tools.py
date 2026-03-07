from __future__ import annotations


def test_internal_registry_includes_csi_tools():
    from universal_agent.tools.internal_registry import get_core_internal_tools

    tools = get_core_internal_tools()
    names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools]

    assert "csi_recent_reports" in names
    assert "csi_opportunity_bundles" in names
    assert "csi_source_health" in names
    assert "csi_watchlist_snapshot" in names
