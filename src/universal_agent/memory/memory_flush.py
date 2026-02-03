from __future__ import annotations

import os
from typing import Optional

from .memory_models import MemoryEntry
from .memory_store import append_memory_entry


def _extract_transcript_tail(
    transcript_path: str,
    max_chars: int = 4000,
    max_lines: int = 120,
) -> str:
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail_lines = lines[-max_lines:] if max_lines > 0 else lines
        content = "".join(tail_lines).strip()
        if max_chars > 0 and len(content) > max_chars:
            content = content[-max_chars:]
        return content.strip()
    except Exception:
        return ""


def flush_pre_compact_memory(
    *,
    workspace_dir: str,
    session_id: Optional[str],
    transcript_path: Optional[str],
    trigger: str,
    max_chars: int = 4000,
) -> Optional[MemoryEntry]:
    content = _extract_transcript_tail(transcript_path or "", max_chars=max_chars)
    if not content:
        return None

    entry = MemoryEntry(
        content=content,
        source="pre_compact",
        session_id=session_id,
        tags=["pre_compact", f"trigger:{trigger}"],
    )
    append_memory_entry(workspace_dir, entry, max_chars=max_chars)
    return entry
