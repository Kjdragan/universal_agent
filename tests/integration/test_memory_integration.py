import os
from pathlib import Path

import pytest

from universal_agent.memory.memory_flush import flush_pre_compact_memory
from universal_agent.memory.memory_context import build_file_memory_context
from universal_agent.memory.memory_store import ensure_memory_scaffold


@pytest.mark.integration
def test_memory_flush_then_retrieve(temp_workspace):
    """
    Integration: simulate a CLI run that writes a transcript, flush it to file memory,
    then verify the next "run" can retrieve context from the file memory index.
    """
    workspace_dir = str(temp_workspace)
    ensure_memory_scaffold(workspace_dir)

    transcript_path = Path(workspace_dir) / "run.log"
    transcript_content = "\n".join(
        [
            "USER: hello",
            "ASSISTANT: hi there",
            "USER: remember that my project is Universal Agent",
            "ASSISTANT: noted",
        ]
    )
    transcript_path.write_text(transcript_content, encoding="utf-8")

    entry = flush_pre_compact_memory(
        workspace_dir=workspace_dir,
        session_id="session_123",
        transcript_path=str(transcript_path),
        trigger="test",
        max_chars=2000,
    )

    assert entry is not None
    assert "Universal Agent" in entry.content

    # Simulate next run retrieval
    context = build_file_memory_context(
        workspace_dir=workspace_dir,
        max_tokens=400,
        index_mode="json",
        recent_limit=5,
    )

    assert context
    assert "FILE MEMORY" in context
