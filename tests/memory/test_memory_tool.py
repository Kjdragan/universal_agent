from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.memory.orchestrator import get_memory_orchestrator
from universal_agent.tools.memory import memory_get, memory_get_wrapper, memory_search, memory_search_wrapper


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("AGENT_WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def test_memory_get_reads_allowed_paths(workspace: Path) -> None:
    (workspace / "MEMORY.md").write_text("Line 1\nLine 2\nLine 3\n", encoding="utf-8")
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    (workspace / "memory" / "notes.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    full = memory_get("MEMORY.md")
    assert "Line 1" in full
    assert "Line 3" in full

    sliced = memory_get("memory/notes.md", from_line=2, lines=1)
    assert sliced.strip() == "beta"


def test_memory_get_blocks_unsafe_paths(workspace: Path) -> None:
    (workspace / "random.txt").write_text("blocked", encoding="utf-8")

    escaped = memory_get("../secret.txt")
    assert "Memory read error" in escaped
    assert "escapes memory root" in escaped

    disallowed = memory_get("random.txt")
    assert "Memory read error" in disallowed
    assert "path must be MEMORY.md or memory/*" in disallowed


def test_memory_search_returns_canonical_result_shape(workspace: Path) -> None:
    broker = get_memory_orchestrator(str(workspace))
    entry = broker.write(
        content="User prefers teal themes for dashboard work.",
        source="test",
        session_id="session_123",
        tags=["preference"],
        memory_class="long_term",
        importance=1.0,
    )
    assert entry is not None

    output = memory_search("teal themes", limit=3)
    assert "# Memory Search Results" in output
    assert "provider=" in output
    assert "model=" in output
    assert "fallback=" in output


@pytest.mark.asyncio
async def test_memory_wrappers_return_tool_payload(workspace: Path) -> None:
    (workspace / "MEMORY.md").write_text("line a\nline b\n", encoding="utf-8")

    get_result = await memory_get_wrapper.handler({"path": "MEMORY.md", "from": 1, "lines": 1})
    text = get_result["content"][0]["text"]
    assert text.strip() == "line a"

    broker = get_memory_orchestrator(str(workspace))
    broker.write(
        content="Transcript memory about roadmap milestones.",
        source="test",
        session_id="session_456",
        tags=["roadmap"],
        memory_class="long_term",
        importance=1.0,
    )
    search_result = await memory_search_wrapper.handler({"query": "roadmap", "limit": 2})
    search_text = search_result["content"][0]["text"]
    assert "# Memory Search Results" in search_text
