"""Polling scheduler scaffold.

Runs each registered job as its own periodic async loop. Hardened against the
2026-07-14 wedge: ``batch_brief`` (the emit step) silently stopped for 14h while
the process stayed alive and other jobs kept running — a single job loop had
died/stalled with no supervision. Three guards prevent that recurring:

  1. **Per-iteration timeout** — a wedged job call (LLM / HTTP / DB lock that
     slips past its own timeout) is force-cancelled so the loop keeps its
     cadence instead of blocking forever.
  2. **Loop never dies on a job error** — job exceptions are caught and logged;
     only a real shutdown cancellation propagates.
  3. **Supervisor** — a backstop task respawns any job loop that dies for *any*
     reason (exotic BaseException, stray cancellation) while the scheduler is
     still running.

Per-job ``last_success`` / ``last_attempt`` monotonic timestamps are tracked so
a stalled job is externally observable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import os
import time

logger = logging.getLogger(__name__)

# Hard ceiling on a single job iteration. No legitimate CSI job (RSS poll,
# batch_brief with a bounded LLM call, dedupe cleanup) runs anywhere near this;
# it exists solely to break a true wedge. Override via env for tuning.
_DEFAULT_JOB_TIMEOUT_SECONDS = 1800.0  # 30 min

# How often the supervisor checks for dead job loops.
_SUPERVISOR_INTERVAL_SECONDS = 30.0


def _default_job_timeout() -> float:
    raw = (os.environ.get("CSI_SCHEDULER_JOB_TIMEOUT_SECONDS") or "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_JOB_TIMEOUT_SECONDS


class PollingScheduler:
    """Simple cooperative polling loop runner (wedge-resistant)."""

    def __init__(self, *, job_timeout_seconds: float | None = None) -> None:
        self._stop_event = asyncio.Event()
        self._job_timeout = (
            _default_job_timeout()
            if job_timeout_seconds is None
            else max(1.0, float(job_timeout_seconds))
        )
        # name -> (interval, func) so the supervisor can respawn a dead loop.
        self._specs: dict[str, tuple[float, Callable[[], Awaitable[None]]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._last_attempt: dict[str, float] = {}
        self._last_success: dict[str, float] = {}
        self._supervisor: asyncio.Task[None] | None = None

    def add_job(
        self,
        name: str,
        interval_seconds: float,
        func: Callable[[], Awaitable[None]],
    ) -> None:
        interval = max(1.0, float(interval_seconds))
        self._specs[name] = (interval, func)
        self._tasks[name] = self._spawn(name)
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(
                self._supervise(), name="csi-scheduler-supervisor"
            )

    def _spawn(self, name: str) -> asyncio.Task[None]:
        interval, func = self._specs[name]
        return asyncio.create_task(
            self._runner(name, interval, func), name=f"csi-job-{name}"
        )

    async def _runner(
        self,
        name: str,
        interval: float,
        func: Callable[[], Awaitable[None]],
    ) -> None:
        while not self._stop_event.is_set():
            self._last_attempt[name] = time.monotonic()
            try:
                await asyncio.wait_for(func(), timeout=self._job_timeout)
            except asyncio.CancelledError:
                if self._stop_event.is_set():
                    raise  # real shutdown
                # A stray cancellation that is NOT shutdown — keep the loop alive.
                logger.error(
                    "Scheduler job cancelled unexpectedly name=%s; continuing", name
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Scheduler job TIMED OUT name=%s after %.0fs; iteration"
                    " abandoned, loop continues",
                    name,
                    self._job_timeout,
                )
            except Exception as exc:  # noqa: BLE001 — a job must never kill its own loop
                logger.error(
                    "Scheduler job failed name=%s error=%r", name, exc, exc_info=True
                )
            else:
                self._last_success[name] = time.monotonic()

            # Reschedule wait. A timeout means "time for the next run"; a set
            # stop_event means shutdown (loop condition exits).
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def _supervise(self) -> None:
        """Respawn any job loop that has died while we're still running."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=_SUPERVISOR_INTERVAL_SECONDS
                )
                return  # stop requested
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                return
            for name, task in list(self._tasks.items()):
                if task.done():
                    cancelled = task.cancelled()
                    exc = None if cancelled else task.exception()
                    logger.error(
                        "Scheduler job loop died name=%s cancelled=%s exc=%r —"
                        " respawning",
                        name,
                        cancelled,
                        exc,
                    )
                    self._tasks[name] = self._spawn(name)

    def job_heartbeats(self) -> dict[str, dict[str, float | None]]:
        """Per-job last attempt/success (monotonic seconds) for observability."""
        return {
            name: {
                "last_attempt": self._last_attempt.get(name),
                "last_success": self._last_success.get(name),
            }
            for name in self._specs
        }

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = list(self._tasks.values())
        if self._supervisor is not None:
            tasks.append(self._supervisor)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._supervisor = None
