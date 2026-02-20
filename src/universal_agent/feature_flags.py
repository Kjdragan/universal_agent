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


def memory_index_enabled(default: bool = True) -> bool:
    """Compatibility helper: canonical memory always controls indexing."""
    return memory_enabled(default=default)


def cron_enabled(default: bool = False) -> bool:
    """Return True only when cron is explicitly enabled."""
    if _is_truthy(os.getenv("UA_DISABLE_CRON")):
        return False
    if _is_truthy(os.getenv("UA_ENABLE_CRON")):
        return True
    return default


def memory_enabled(default: bool = True) -> bool:
    """Return canonical memory enablement."""
    if _is_truthy(os.getenv("UA_DISABLE_MEMORY")):
        return False
    if _is_truthy(os.getenv("UA_MEMORY_ENABLED")):
        return True
    return default


def memory_index_mode(default: str = "json") -> str:
    """Return canonical index mode used by memory subsystem."""
    if not memory_enabled(default=True):
        return "off"
    return "vector"


def memory_provider(default: str = "auto") -> str:
    """Return configured provider preference for canonical memory."""
    return _read_choice(
        "UA_MEMORY_PROVIDER",
        ("auto", "local", "openai", "gemini", "voyage"),
        default,
    )


def memory_max_tokens(default: int = 800) -> int:
    """Return max tokens allowed for memory injection."""
    raw = os.getenv("UA_MEMORY_MAX_TOKENS")
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def memory_flush_enabled(default: bool = True) -> bool:
    """Return True when canonical pre-compaction memory flush is enabled."""
    if _is_truthy(os.getenv("UA_DISABLE_MEMORY_FLUSH_ENABLED")):
        return False
    if _is_truthy(os.getenv("UA_MEMORY_FLUSH_ENABLED")):
        return True
    return default


def memory_flush_soft_threshold_tokens(default: int = 4000) -> int:
    """Token distance from compaction threshold that triggers memory flush."""
    return _read_int("UA_MEMORY_FLUSH_SOFT_THRESHOLD_TOKENS", default, minimum=0)


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


def _read_csv_list(name: str, *, allowed: Iterable[str] | None = None) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if allowed is None:
        return parts
    allowed_set = {item.lower() for item in allowed}
    return [item for item in parts if item.lower() in allowed_set]


def memory_session_enabled(default: bool = True) -> bool:
    """Enable transcript/session memory indexing/search."""
    if _is_truthy(os.getenv("UA_MEMORY_SESSION_DISABLED")):
        return False
    if _is_truthy(os.getenv("UA_MEMORY_SESSION_ENABLED")):
        return True
    return default


def memory_session_sources(default: tuple[str, ...] = ("memory", "sessions")) -> list[str]:
    """Return requested search sources for canonical memory."""
    raw = (os.getenv("UA_MEMORY_SOURCES") or "").strip()
    if not raw:
        return list(default)
    parts = [item.strip().lower() for item in raw.split(",") if item.strip()]
    filtered = [item for item in parts if item in {"memory", "sessions"}]
    return filtered or list(default)


def memory_session_index_on_end(default: bool = True) -> bool:
    """Run forced transcript indexing pass at session end."""
    return memory_session_enabled(default=default)


def memory_session_delta_bytes(default: int = 100_000) -> int:
    """Minimum appended bytes before background reindex."""
    return _read_int("UA_MEMORY_SESSION_DELTA_BYTES", default, minimum=0)


def memory_session_delta_messages(default: int = 50) -> int:
    """Minimum appended message lines before background reindex."""
    return _read_int("UA_MEMORY_SESSION_DELTA_MESSAGES", default, minimum=0)


def memory_scope(default: str = "direct_only") -> str:
    """Return memory retrieval scope."""
    return _read_choice("UA_MEMORY_SCOPE", ("direct_only", "all"), default)


def memory_retrieval_strategy(default: str = "semantic_first") -> str:
    """Compatibility helper for canonical retrieval."""
    return "semantic_first"


def memory_backend(default: str = "chromadb") -> str:
    """Compatibility helper used by existing vector index codepaths."""
    provider = memory_provider(default="auto")
    if provider == "local":
        return "lancedb"
    return "chromadb"


def memory_orchestrator_enabled(default: bool = True) -> bool:
    """Compatibility helper: canonical orchestrator is always active when memory is on."""
    return memory_enabled(default=default)


def memory_adapter_state(adapter_name: str, default: str = "off") -> str:
    """Compatibility helper after hard-cut adapter removal."""
    normalized = adapter_name.strip().lower()
    if normalized in {"ua_file_memory", "canonical"}:
        return "active"
    return "off"


def memory_profile_mode(default: str = "prod") -> str:
    """Compatibility helper retained for import stability."""
    return default


def memory_tag_dev_writes(default: bool = False) -> bool:
    """Compatibility helper retained for import stability."""
    return default


def memory_runtime_tags(default: tuple[str, ...] = ()) -> list[str]:
    """Compatibility helper retained for import stability."""
    return list(default)


def memory_long_term_tag_allowlist(default: tuple[str, ...] = ()) -> list[str]:
    """Compatibility helper retained for import stability."""
    return list(default)


def memory_write_policy_min_importance(default: float = 0.0) -> float:
    """Compatibility helper retained for import stability."""
    return _read_float("UA_MEMORY_WRITE_MIN_IMPORTANCE", default, minimum=0.0)


def memory_rerank_enabled(default: bool = False) -> bool:
    """Compatibility helper retained for import stability."""
    return default


def memory_embedding_provider(default: str = "auto") -> str:
    """Compatibility helper retained for import stability."""
    return memory_provider(default=default)


def memory_embedding_query_intent(default: bool = True) -> bool:
    """Compatibility helper retained for import stability."""
    return default


def memory_embedding_document_intent(default: bool = True) -> bool:
    """Compatibility helper retained for import stability."""
    return default


def memory_embedding_batch_enabled(default: bool = True) -> bool:
    """Compatibility helper retained for import stability."""
    return default


def memory_embedding_batch_failure_threshold(default: int = 3) -> int:
    """Compatibility helper retained for import stability."""
    return _read_int("UA_MEMORY_EMBEDDING_BATCH_FAILURE_THRESHOLD", default, minimum=1)


def memory_embedding_fallback_to_non_batch(default: bool = True) -> bool:
    """Compatibility helper retained for import stability."""
    return default


def memory_flush_on_exit(default: bool = True) -> bool:
    """Compatibility helper retained while callsites are updated."""
    return memory_flush_enabled(default=default)


def memory_flush_max_chars(default: int = 4000) -> int:
    """Compatibility helper retained while callsites are updated."""
    return _read_int("UA_MEMORY_FLUSH_MAX_CHARS", default, minimum=0)


def coder_vp_enabled(default: bool = False) -> bool:
    """Enable CODER VP routing lane (Phase A)."""
    if _is_truthy(os.getenv("UA_DISABLE_CODER_VP")):
        return False
    if _is_truthy(os.getenv("UA_ENABLE_CODER_VP")):
        return True
    return default


def coder_vp_shadow_mode(default: bool = False) -> bool:
    """Run CODER VP routing in shadow mode (no user-visible delegation)."""
    if _is_truthy(os.getenv("UA_DISABLE_CODER_VP_SHADOW_MODE")):
        return False
    if _is_truthy(os.getenv("UA_CODER_VP_SHADOW_MODE")):
        return True
    return default


def coder_vp_force_fallback(default: bool = False) -> bool:
    """Force all eligible CODER VP requests to fallback path."""
    if _is_truthy(os.getenv("UA_DISABLE_CODER_VP_FORCE_FALLBACK")):
        return False
    if _is_truthy(os.getenv("UA_CODER_VP_FORCE_FALLBACK")):
        return True
    return default


def coder_vp_id(default: str = "vp.coder.primary") -> str:
    """Stable registry ID for the Phase A CODER VP lane."""
    value = (os.getenv("UA_CODER_VP_ID") or "").strip()
    return value or default


def coder_vp_runtime_id(default: str = "runtime.coder_vp.inprocess") -> str:
    """Runtime lane identifier for CODER VP."""
    value = (os.getenv("UA_CODER_VP_RUNTIME_ID") or "").strip()
    return value or default


def coder_vp_workspace_dir(default: str = "") -> str:
    """Optional absolute workspace override for CODER VP lane."""
    value = (os.getenv("UA_CODER_VP_WORKSPACE_DIR") or "").strip()
    if value:
        return value
    return default


def coder_vp_display_name(default: str = "CODIE") -> str:
    """Human-readable lane name for user-facing status and docs."""
    value = (os.getenv("UA_CODER_VP_DISPLAY_NAME") or "").strip()
    return value or default


def coder_vp_lease_ttl_seconds(default: int = 300) -> int:
    """Lease TTL for CODER VP session ownership."""
    return _read_int("UA_CODER_VP_LEASE_TTL_SECONDS", default, minimum=30)
