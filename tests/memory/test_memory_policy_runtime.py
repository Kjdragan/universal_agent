from __future__ import annotations

from pathlib import Path

from universal_agent.execution_engine import _build_memory_env_overrides
from universal_agent.memory.orchestrator import MemoryOrchestrator


def test_build_memory_env_overrides_modes():
    off = _build_memory_env_overrides({"mode": "off"})
    assert off["UA_DISABLE_MEMORY"] == "1"
    assert off["UA_MEMORY_SESSION_DISABLED"] == "1"

    session_only = _build_memory_env_overrides({"mode": "session_only", "tags": ["dev_test"]})
    assert session_only["UA_MEMORY_ENABLED"] == "1"
    assert session_only["UA_MEMORY_PROFILE_MODE"] == "dev_no_persist"
    assert session_only["UA_MEMORY_RUN_TAGS"] == "dev_test"

    selective = _build_memory_env_overrides({"mode": "selective", "long_term_tag_allowlist": ["retain"]})
    assert selective["UA_MEMORY_PROFILE_MODE"] == "dev_memory_test"
    assert selective["UA_MEMORY_LONG_TERM_TAG_ALLOWLIST"] == "retain"

    full = _build_memory_env_overrides({"mode": "full"})
    assert full["UA_MEMORY_PROFILE_MODE"] == "prod"
    assert full["UA_DISABLE_MEMORY"] is None


def test_selective_mode_blocks_long_term_without_allow_tag(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_MEMORY_PROFILE_MODE", "dev_memory_test")
    monkeypatch.setenv("UA_MEMORY_LONG_TERM_TAG_ALLOWLIST", "retain")
    monkeypatch.setenv("UA_MEMORY_RUN_TAGS", "dev_test")

    broker = MemoryOrchestrator(str(tmp_path))

    blocked = broker.write(
        content="do not persist",
        source="test",
        session_id="session-test",
        tags=["pre_compact"],
        memory_class="long_term",
        importance=1.0,
    )
    assert blocked is None

    allowed = broker.write(
        content="persist me",
        source="test",
        session_id="session-test",
        tags=["retain"],
        memory_class="long_term",
        importance=1.0,
    )
    assert allowed is not None
    assert "retain" in allowed.tags
    assert "dev_test" in allowed.tags


def test_session_sync_still_indexes_when_long_term_persist_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_MEMORY_PROFILE_MODE", "dev_no_persist")

    broker = MemoryOrchestrator(str(tmp_path))
    transcript = tmp_path / "transcript.md"
    transcript.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

    result = broker.sync_session(
        session_id="session-sync",
        transcript_path=str(transcript),
        force=True,
    )
    assert result["indexed"] is True
    assert result["reason"] == "indexed"
