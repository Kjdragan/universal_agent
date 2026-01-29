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
