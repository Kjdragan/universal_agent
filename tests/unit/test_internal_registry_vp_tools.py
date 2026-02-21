from __future__ import annotations


def test_internal_registry_includes_vp_tools():
    from universal_agent.tools.internal_registry import get_core_internal_tools

    tools = get_core_internal_tools()
    names = [getattr(tool, "name", getattr(tool, "__name__", str(tool))) for tool in tools]

    assert "vp_dispatch_mission" in names
    assert "vp_get_mission" in names
    assert "vp_list_missions" in names
    assert "vp_wait_mission" in names
    assert "vp_cancel_mission" in names
    assert "vp_read_result_artifacts" in names
