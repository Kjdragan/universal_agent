from __future__ import annotations

import logging
import os

from universal_agent.wiki.core import sync_internal_memory_vault

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def maybe_auto_sync_internal_memory_vault(*, trigger: str) -> dict | None:
    if not _env_truthy("UA_LLM_WIKI_AUTO_SYNC_INTERNAL", default=False):
        return None
    if not _env_truthy("UA_LLM_WIKI_ENABLE_INTERNAL_PROJECTION", default=True):
        return None
    try:
        return sync_internal_memory_vault(trigger=trigger)
    except Exception:
        logger.exception("internal_memory_wiki_auto_sync_failed trigger=%s", trigger)
        return None
