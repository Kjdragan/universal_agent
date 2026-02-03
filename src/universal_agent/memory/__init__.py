"""File-based memory helpers for Universal Agent."""

from .memory_models import MemoryEntry
from .memory_store import (
    MemoryPaths,
    ensure_memory_scaffold,
    append_memory_entry,
    update_recent_context_section,
)
from .memory_index import (
    load_index,
    append_index_entry,
    recent_entries,
    search_entries,
)
from .memory_context import build_file_memory_context
from .memory_flush import flush_pre_compact_memory

__all__ = [
    "MemoryEntry",
    "MemoryPaths",
    "ensure_memory_scaffold",
    "append_memory_entry",
    "update_recent_context_section",
    "load_index",
    "append_index_entry",
    "recent_entries",
    "search_entries",
    "build_file_memory_context",
    "flush_pre_compact_memory",
]
