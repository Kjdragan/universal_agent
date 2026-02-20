from .memory_vector_index import search_vectors, upsert_vector
from .memory_store import append_memory_entry, ensure_memory_scaffold
from .memory_models import MemoryEntry
try:
    from .chromadb_backend import get_memory as get_chroma_memory
except Exception:  # pragma: no cover - optional dependency
    def get_chroma_memory(*_args, **_kwargs):  # type: ignore
        raise RuntimeError("chromadb backend is unavailable in this environment")

__all__ = [
    "search_vectors",
    "upsert_vector",
    "append_memory_entry",
    "ensure_memory_scaffold",
    "MemoryEntry",
    "get_chroma_memory",
]
