from __future__ import annotations

from pathlib import Path

from universal_agent.execution_engine import _build_memory_env_overrides
from universal_agent.memory.orchestrator import MemoryOrchestrator
from universal_agent.session_policy import default_memory_policy, normalize_memory_policy


def test_build_memory_env_overrides_disabled_mode() -> None:
    overrides = _build_memory_env_overrides({"enabled": False, "sessionMemory": False})
    assert overrides["UA_DISABLE_MEMORY"] == "1"
    assert overrides["UA_MEMORY_ENABLED"] == "0"
    assert overrides["UA_MEMORY_SESSION_DISABLED"] == "1"
    assert overrides["UA_MEMORY_SESSION_ENABLED"] is None


def test_build_memory_env_overrides_enabled_with_scope_and_sources() -> None:
    overrides = _build_memory_env_overrides(
        {
            "enabled": True,
            "sessionMemory": True,
            "scope": "all",
            "sources": ["sessions", "memory", "invalid"],
        }
    )
    assert overrides["UA_DISABLE_MEMORY"] is None
    assert overrides["UA_MEMORY_ENABLED"] == "1"
    assert overrides["UA_MEMORY_SESSION_ENABLED"] == "1"
    assert overrides["UA_MEMORY_SESSION_DISABLED"] is None
    assert overrides["UA_MEMORY_SCOPE"] == "all"
    assert overrides["UA_MEMORY_SOURCES"] == "sessions,memory"


def test_memory_policy_normalization_uses_canonical_schema() -> None:
    defaults = default_memory_policy()
    assert defaults == {
        "enabled": True,
        "sessionMemory": True,
        "sources": ["memory", "sessions"],
        "scope": "direct_only",
    }

    normalized = normalize_memory_policy(
        {
            "enabled": "yes",
            "session_memory_enabled": 0,
            "sources": "memory,invalid,sessions",
            "scope": "invalid_scope",
        }
    )
    assert normalized["enabled"] is True
    assert normalized["sessionMemory"] is False
    assert normalized["sources"] == ["memory", "sessions"]
    assert normalized["scope"] == "direct_only"


def test_direct_only_scope_blocks_indirect_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UA_MEMORY_SCOPE", "direct_only")
    broker = MemoryOrchestrator(str(tmp_path))

    written = broker.write(
        content="Sensitive direct-session memory.",
        source="test",
        session_id="s1",
        tags=["sensitive"],
        memory_class="long_term",
        importance=1.0,
    )
    assert written is not None

    blocked = broker.search(query="sensitive", limit=5, sources=["memory"], direct_context=False)
    assert blocked == []

    allowed = broker.search(query="sensitive", limit=5, sources=["memory"], direct_context=True)
    assert allowed
