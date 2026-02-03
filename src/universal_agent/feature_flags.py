"""Feature flags and kill switches for Universal Agent.

These are lightweight placeholders to keep defaults safe (off) until features
are explicitly enabled. They are intentionally simple and side-effect free.
"""

from __future__ import annotations

import os

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
    """Return the configured memory index mode (json|fts|off)."""
    mode = (os.getenv("UA_MEMORY_INDEX") or "").strip().lower()
    if mode in {"off", "false", "0"}:
        return "off"
    if mode in {"json", "fts"}:
        return mode
    if memory_index_enabled(default=False):
        return default
    return "off"


def memory_max_tokens(default: int = 800) -> int:
    """Return max tokens allowed for memory injection."""
    raw = os.getenv("UA_MEMORY_MAX_TOKENS")
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default
