from __future__ import annotations

from pathlib import Path

from universal_agent.memory.memory_flush import flush_pre_compact_memory
from universal_agent.memory.memory_models import MemoryEntry
from universal_agent.tools.memory import ua_memory_search


def test_ua_memory_search_uses_orchestrator_when_unified(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_MEMORY_ORCHESTRATOR_MODE", "unified")
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))

    captured: dict[str, object] = {}

    class DummyBroker:
        def search(self, *, query: str, limit: int, sources: list[str]):
            captured["query"] = query
            captured["limit"] = limit
            captured["sources"] = list(sources)
            return [{"source": "memory", "timestamp": "2026-02-07", "summary": "hit", "score": 0.9}]

        def format_search_results(self, hits):
            assert hits
            return "BROKER_RESULT"

    import universal_agent.memory.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "get_memory_orchestrator", lambda workspace_dir=None: DummyBroker())

    result = ua_memory_search("test query", limit=3)
    assert result == "BROKER_RESULT"
    assert captured["query"] == "test query"
    assert captured["limit"] == 3
    assert captured["sources"] == ["memory", "sessions"]


def test_flush_pre_compact_uses_orchestrator_when_unified(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_MEMORY_ORCHESTRATOR_MODE", "unified")

    expected = MemoryEntry(content="captured", source="pre_compact")

    class DummyBroker:
        def flush_pre_compact(
            self,
            *,
            session_id: str | None,
            transcript_path: str | None,
            trigger: str,
            max_chars: int = 4000,
        ):
            assert session_id == "sess-1"
            assert trigger == "test"
            assert max_chars == 1234
            assert transcript_path is not None
            return expected

    import universal_agent.memory.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "get_memory_orchestrator", lambda workspace_dir=None: DummyBroker())

    transcript = Path(tmp_path) / "transcript.md"
    transcript.write_text("hello", encoding="utf-8")

    result = flush_pre_compact_memory(
        workspace_dir=str(tmp_path),
        session_id="sess-1",
        transcript_path=str(transcript),
        trigger="test",
        max_chars=1234,
    )
    assert result is expected
