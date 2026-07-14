"""Resilience tests for the CSI PollingScheduler.

Locks the 2026-07-14 wedge fix: a single job loop must never silently stop.
  1. A job that raises every iteration must keep being rescheduled.
  2. A job that hangs must be force-cancelled by the per-iteration timeout and
     retried, not block its loop forever.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHED_PATH = REPO_ROOT / "CSI_Ingester" / "development" / "csi_ingester" / "scheduler.py"


def _load_scheduler():
    spec = importlib.util.spec_from_file_location("csi_sched_under_test", SCHED_PATH)
    assert spec and spec.loader, "scheduler module spec not loadable"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_job_exception_does_not_kill_the_loop():
    mod = _load_scheduler()

    async def scenario() -> int:
        sched = mod.PollingScheduler(job_timeout_seconds=2.0)
        calls: list[int] = []

        async def flaky() -> None:
            calls.append(1)
            raise RuntimeError("boom")  # every iteration fails

        sched.add_job("flaky", 1.0, flaky)  # interval floors at 1.0s
        # first run is immediate, then one per ~1s. 3.2s -> at least 3 runs.
        await asyncio.sleep(3.2)
        await sched.stop()
        return len(calls)

    runs = asyncio.run(scenario())
    assert runs >= 3, f"loop stopped rescheduling after an exception (runs={runs})"


def test_hung_job_is_timed_out_and_retried():
    mod = _load_scheduler()

    async def scenario() -> int:
        sched = mod.PollingScheduler(job_timeout_seconds=0.5)
        calls: list[int] = []

        async def hang() -> None:
            calls.append(1)
            await asyncio.sleep(30)  # would wedge the loop without a timeout

        sched.add_job("hang", 1.0, hang)
        # each cycle = 0.5s timeout + 1.0s interval = ~1.5s -> ~2 runs in 3.2s.
        await asyncio.sleep(3.2)
        await sched.stop()
        return len(calls)

    runs = asyncio.run(scenario())
    assert runs >= 2, f"hung job was not timed out and retried (runs={runs})"


def test_stop_is_clean():
    mod = _load_scheduler()

    async def scenario() -> None:
        sched = mod.PollingScheduler()

        async def noop() -> None:
            return None

        sched.add_job("noop", 1.0, noop)
        await asyncio.sleep(0.1)
        await sched.stop()  # must not raise; must cancel all tasks

    asyncio.run(scenario())
