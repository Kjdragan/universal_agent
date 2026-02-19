from __future__ import annotations

import shutil
from pathlib import Path

import universal_agent.memory.orchestrator as orchestrator
from universal_agent.memory.memory_context import build_file_memory_context
from universal_agent.memory.paths import resolve_shared_memory_workspace


def test_shared_memory_survives_session_workspace_cleanup(monkeypatch, tmp_path: Path):
    phrase = "phoenix-memory-persistence-check"
    shared_root = (tmp_path / "shared_memory_root").resolve()
    monkeypatch.setenv("UA_SHARED_MEMORY_DIR", str(shared_root))
    monkeypatch.setenv("UA_MEMORY_ORCHESTRATOR_MODE", "unified")
    orchestrator._BROKERS.clear()

    session_a = (tmp_path / "AGENT_RUN_WORKSPACES" / "session_a").resolve()
    session_a.mkdir(parents=True, exist_ok=True)
    transcript_path = session_a / "transcript.md"
    transcript_path.write_text(f"user memory line: {phrase}\n", encoding="utf-8")

    broker_a = orchestrator.get_memory_orchestrator(
        workspace_dir=resolve_shared_memory_workspace(str(session_a))
    )
    indexed = broker_a.sync_session(
        session_id="session_a",
        transcript_path=str(transcript_path),
        force=True,
    )
    assert indexed["indexed"] is True
    persisted = broker_a.write(
        content=f"long term memory line: {phrase}",
        source="test",
        session_id="session_a",
        tags=["retain"],
        memory_class="long_term",
        importance=1.0,
    )
    assert persisted is not None

    shutil.rmtree(session_a)
    orchestrator._BROKERS.clear()

    session_b = (tmp_path / "AGENT_RUN_WORKSPACES" / "session_b").resolve()
    broker_b = orchestrator.get_memory_orchestrator(
        workspace_dir=resolve_shared_memory_workspace(str(session_b))
    )
    hits = broker_b.search(query=phrase, limit=5, sources=["sessions"])
    assert hits, "Expected persisted session memory hit after workspace cleanup"
    combined = " ".join((hit.get("summary") or hit.get("preview") or "") for hit in hits)
    assert phrase in combined

    context = build_file_memory_context(
        str(shared_root),
        max_tokens=400,
        index_mode="json",
        recent_limit=10,
    )
    assert phrase in context
