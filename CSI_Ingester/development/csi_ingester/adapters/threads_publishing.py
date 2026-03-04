"""Threads publishing interface scaffold (phase 2 gate)."""

from __future__ import annotations

import os
from typing import Any


class ThreadsPublishingDisabledError(RuntimeError):
    """Raised when publishing operations are called before phase-2 enablement."""


class ThreadsPublishingInterface:
    """Internal publishing contract for future phase-2 automation.

    Phase 1 keeps this interface disabled by default. It is intentionally present so
    downstream integrations can code against stable method names without enabling
    write-side effects yet.
    """

    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = bool(enabled) if enabled is not None else (
            str(os.getenv("CSI_THREADS_PUBLISHING_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
        )

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise ThreadsPublishingDisabledError("threads_publishing_disabled_phase1")

    async def create_container(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        return {"status": "not_implemented", "operation": "create_container", "payload": payload}

    async def publish_container(self, creation_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        return {"status": "not_implemented", "operation": "publish_container", "creation_id": creation_id}

    async def reply_to_post(self, media_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        return {
            "status": "not_implemented",
            "operation": "reply_to_post",
            "media_id": media_id,
            "payload": payload,
        }
