from __future__ import annotations

from pathlib import Path

from universal_agent.memory import MemoryEntry
from universal_agent.memory.memory_store import ensure_memory_scaffold, append_memory_entry
from universal_agent.memory.memory_context import build_file_memory_context


def test_append_memory_entry_creates_files(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "session"
    ensure_memory_scaffold(str(workspace_dir))

    entry = MemoryEntry(
        content="User asked about memory persistence.",
        source="pre_compact",
        session_id="session_123",
        tags=["memory", "test"],
    )

    paths = append_memory_entry(str(workspace_dir), entry, max_chars=1000)

    assert Path(paths.memory_dir).exists()
    assert Path(paths.memory_md).exists()
    assert Path(paths.index_path).exists()

    daily_files = list(Path(paths.memory_dir).glob("*.md"))
    assert daily_files, "Expected daily memory file to be created"

    memory_md = Path(paths.memory_md).read_text(encoding="utf-8")
    assert "[RECENT_CONTEXT]" in memory_md
    assert "User asked about memory persistence" in memory_md


def test_build_file_memory_context(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "session"
    entry = MemoryEntry(
        content="Summary of recent task.",
        source="manual",
        session_id="session_abc",
        tags=["summary"],
    )
    append_memory_entry(str(workspace_dir), entry, max_chars=1000)

    context = build_file_memory_context(
        str(workspace_dir), max_tokens=200, index_mode="json", recent_limit=5
    )
    assert "FILE MEMORY" in context
    assert "Summary of recent task" in context
