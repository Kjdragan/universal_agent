"""Feature flags and kill switches for Universal Agent.

These are lightweight placeholders to keep defaults safe (off) until features
are explicitly enabled. They are intentionally simple and side-effect free.
"""

from __future__ import annotations

import os
from typing import Iterable

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def heartbeat_enabled(default: bool = False) -> bool:
    """Return True only when heartbeat is explicitly enabled."""
    if _is_truthy(os.getenv("UA_DISABLE_HEARTBEAT")):
        return False
    if _is_truthy(os.getenv("UA_ENABLE_HEARTBEAT")):
        return True
    return default


def memory_index_enabled(default: bool = False) -> bool:
    """Return True only when memory indexing is explicitly enabled."""
    if _is_truthy(os.getenv("UA_DISABLE_MEMORY_INDEX")):
        return False
    if _is_truthy(os.getenv("UA_ENABLE_MEMORY_INDEX")):
        return True
    return default


def cron_enabled(default: bool = False) -> bool:
    """Return True only when cron is explicitly enabled."""
    if _is_truthy(os.getenv("UA_DISABLE_CRON")):
        return False
    if _is_truthy(os.getenv("UA_ENABLE_CRON")):
        return True
    return default


def memory_enabled(default: bool = False) -> bool:
    """Return True only when memory is explicitly enabled (or index enabled)."""
    if _is_truthy(os.getenv("UA_DISABLE_MEMORY")) or _is_truthy(os.getenv("UA_DISABLE_LOCAL_MEMORY")):
        return False
    if _is_truthy(os.getenv("UA_MEMORY_ENABLED")):
        return True
    if memory_index_enabled(default=False):
        return True
    return default


def memory_index_mode(default: str = "json") -> str:
    """Return the configured memory index mode (json|vector|fts|off)."""
    mode = (os.getenv("UA_MEMORY_INDEX") or "").strip().lower()
    if mode in {"off", "false", "0"}:
        return "off"
    if mode in {"json", "vector", "fts"}:
        return mode
    if memory_index_enabled(default=False):
        return default
    return "off"


def memory_backend(default: str = "chromadb") -> str:
    """Return the configured memory backend (chromadb|lancedb|sqlite).
    
    chromadb: Uses ChromaDB with real embeddings for semantic search (default, CPU-compatible)
    lancedb: Uses LanceDB with real embeddings (requires AVX2 CPU instructions)
    sqlite: Uses SQLite with hash-based embeddings (legacy)
    """
    backend = (os.getenv("UA_MEMORY_BACKEND") or "").strip().lower()
    if backend in {"chromadb", "lancedb", "sqlite"}:
        return backend
    return default


def memory_max_tokens(default: int = 800) -> int:
    """Return max tokens allowed for memory injection."""
    raw = os.getenv("UA_MEMORY_MAX_TOKENS")
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def memory_flush_on_exit(default: bool = False) -> bool:
    """Return True when post-run memory flush is enabled."""
    if _is_truthy(os.getenv("UA_DISABLE_MEMORY_FLUSH_ON_EXIT")):
        return False
    if _is_truthy(os.getenv("UA_MEMORY_FLUSH_ON_EXIT")):
        return True
    return default


def memory_flush_max_chars(default: int = 4000) -> int:
    """Return max chars for memory flush content."""
    raw = os.getenv("UA_MEMORY_FLUSH_MAX_CHARS")
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _read_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _read_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _read_choice(name: str, allowed: Iterable[str], default: str) -> str:
    choices = {item.lower() for item in allowed}
    value = (os.getenv(name) or "").strip().lower()
    if value in choices:
        return value
    return default


def memory_orchestrator_mode(default: str = "legacy") -> str:
    """Return memory control mode: legacy|unified."""
    return _read_choice("UA_MEMORY_ORCHESTRATOR_MODE", ("legacy", "unified"), default)


def memory_orchestrator_enabled(default: bool = True) -> bool:
    """Return True when orchestrator is active."""
    mode = memory_orchestrator_mode(default="legacy")
    if mode == "unified":
        return True
    if _is_truthy(os.getenv("UA_MEMORY_ORCHESTRATOR_ENABLED")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_ORCHESTRATOR_DISABLED")):
        return False
    return default and mode != "legacy"


def memory_adapter_state(adapter_name: str, default: str = "off") -> str:
    """Return adapter state: active|shadow|off|deprecated."""
    normalized = adapter_name.strip().upper().replace("-", "_")
    key = f"UA_MEMORY_ADAPTER_{normalized}_STATE"
    return _read_choice(key, ("active", "shadow", "off", "deprecated"), default)


def memory_profile_mode(default: str = "dev_standard") -> str:
    """Return memory profile mode."""
    return _read_choice(
        "UA_MEMORY_PROFILE_MODE",
        ("prod", "dev_standard", "dev_memory_test", "dev_no_persist"),
        default,
    )


def memory_tag_dev_writes(default: bool = True) -> bool:
    """Tag writes with profile/env metadata for dev modes."""
    if _is_truthy(os.getenv("UA_MEMORY_TAG_DEV_WRITES")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_DISABLE_TAG_DEV_WRITES")):
        return False
    return default


def memory_write_policy_min_importance(default: float = 0.6) -> float:
    """Minimum importance threshold for long-term writes in development profiles."""
    return _read_float("UA_MEMORY_WRITE_MIN_IMPORTANCE", default, minimum=0.0)


def memory_session_enabled(default: bool = True) -> bool:
    """Enable session memory indexing/search."""
    if _is_truthy(os.getenv("UA_MEMORY_SESSION_DISABLED")):
        return False
    if _is_truthy(os.getenv("UA_MEMORY_SESSION_ENABLED")):
        return True
    return default


def memory_session_sources(default: tuple[str, ...] = ("memory", "sessions")) -> list[str]:
    """Return requested memory sources for broker search."""
    raw = (os.getenv("UA_MEMORY_SESSION_SOURCES") or "").strip()
    if not raw:
        return list(default)
    parts = [item.strip().lower() for item in raw.split(",") if item.strip()]
    filtered = [item for item in parts if item in {"memory", "sessions"}]
    return filtered or list(default)


def memory_session_index_on_end(default: bool = True) -> bool:
    """Run forced session indexing pass on session end."""
    if _is_truthy(os.getenv("UA_MEMORY_SESSION_INDEX_ON_END")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_DISABLE_SESSION_INDEX_ON_END")):
        return False
    return default


def memory_session_delta_bytes(default: int = 100_000) -> int:
    """Minimum appended bytes before background reindex."""
    return _read_int("UA_MEMORY_SESSION_DELTA_BYTES", default, minimum=0)


def memory_session_delta_messages(default: int = 50) -> int:
    """Minimum appended message lines before background reindex."""
    return _read_int("UA_MEMORY_SESSION_DELTA_MESSAGES", default, minimum=0)


def memory_retrieval_strategy(default: str = "semantic_first") -> str:
    """Return retrieval strategy: semantic_first|lexical_only|hybrid."""
    return _read_choice(
        "UA_MEMORY_RETRIEVAL_STRATEGY",
        ("semantic_first", "lexical_only", "hybrid"),
        default,
    )


def memory_rerank_enabled(default: bool = False) -> bool:
    """Feature gate for explicit rerank stage."""
    if _is_truthy(os.getenv("UA_MEMORY_RERANK_ENABLED")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_RERANK_DISABLED")):
        return False
    return default


def memory_embedding_provider(default: str = "local") -> str:
    """Preferred embedding provider hint for orchestrator."""
    return _read_choice("UA_MEMORY_EMBEDDING_PROVIDER", ("local", "openai", "gemini", "voyage"), default)


def memory_embedding_query_intent(default: bool = True) -> bool:
    """Enable explicit query-embedding intent when supported."""
    if _is_truthy(os.getenv("UA_MEMORY_EMBEDDING_QUERY_INTENT")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_DISABLE_EMBEDDING_QUERY_INTENT")):
        return False
    return default


def memory_embedding_document_intent(default: bool = True) -> bool:
    """Enable explicit document-embedding intent when supported."""
    if _is_truthy(os.getenv("UA_MEMORY_EMBEDDING_DOCUMENT_INTENT")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_DISABLE_EMBEDDING_DOCUMENT_INTENT")):
        return False
    return default


def memory_embedding_batch_enabled(default: bool = True) -> bool:
    """Enable embedding batch mode where provider supports it."""
    if _is_truthy(os.getenv("UA_MEMORY_EMBEDDING_BATCH_ENABLED")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_DISABLE_EMBEDDING_BATCH")):
        return False
    return default


def memory_embedding_batch_failure_threshold(default: int = 3) -> int:
    """Failure threshold before disabling batch mode."""
    return _read_int("UA_MEMORY_EMBEDDING_BATCH_FAILURE_THRESHOLD", default, minimum=1)


def memory_embedding_fallback_to_non_batch(default: bool = True) -> bool:
    """Fallback to non-batch embedding mode after batch failures."""
    if _is_truthy(os.getenv("UA_MEMORY_EMBEDDING_FALLBACK_NON_BATCH")):
        return True
    if _is_truthy(os.getenv("UA_MEMORY_DISABLE_EMBEDDING_FALLBACK_NON_BATCH")):
        return False
    return default
