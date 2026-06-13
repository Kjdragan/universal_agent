from __future__ import annotations


def test_internal_registry_includes_wiki_tools():
    from universal_agent.tools.internal_registry import get_core_internal_tools

    tools = get_core_internal_tools()
    names = [getattr(tool, "name", getattr(tool, "__name__", str(tool))) for tool in tools]

    assert "wiki_init_vault" in names
    assert "wiki_sync_internal_memory" in names
    assert "wiki_query" in names
    assert "wiki_lint" in names
    # "step 6" of the Create-Wiki flow — the bridge tool an agent must be able
    # to invoke by name to persist a downloaded NLM report into the vault.
    assert "wiki_ingest_external_source" in names

    assert "kb_list" in names
    assert "kb_get" in names
    assert "kb_register" in names
    assert "kb_update" in names
