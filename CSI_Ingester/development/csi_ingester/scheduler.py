"""Polling scheduler scaffold."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class PollingScheduler:
    """Simple cooperative polling loop runner."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()

    def add_job(self, name: str, interval_seconds: float, func: Callable[[], Awaitable[None]]) -> None:
        interval = max(1.0, float(interval_seconds))

        async def _runner() -> None:
            while not self._stop_event.is_set():
                try:
                    await func()
                except Exception as exc:
                    logger.warning("Scheduler job failed name=%s error=%s", name, exc)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue

        self._tasks.append(asyncio.create_task(_runner(), name=f"csi-job-{name}"))

    async def stop(self) -> None:
        self._stop_event.set()
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

