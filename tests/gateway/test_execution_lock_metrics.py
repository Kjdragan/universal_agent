import asyncio

import pytest

from universal_agent.gateway import InProcessGateway


@pytest.mark.asyncio
async def test_execution_lock_metrics_capture_wait_and_hold():
    gateway = InProcessGateway()

    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def hold_first_lock() -> None:
        async with gateway._timed_execution_lock("test_first"):
            first_entered.set()
            await release_first.wait()

    async def wait_for_lock() -> None:
        await first_entered.wait()
        async with gateway._timed_execution_lock("test_second"):
            return

    task_first = asyncio.create_task(hold_first_lock())
    task_second = asyncio.create_task(wait_for_lock())

    await first_entered.wait()
    await asyncio.sleep(0.05)
    release_first.set()
    await asyncio.gather(task_first, task_second)

    snapshot = gateway.execution_runtime_snapshot()
    assert int(snapshot.get("lock_acquire_count", 0)) >= 2
    assert float(snapshot.get("lock_wait_seconds_total", 0.0)) > 0.0
    assert float(snapshot.get("lock_hold_seconds_total", 0.0)) > 0.0
    assert int(snapshot.get("lock_waiters_peak", 0)) >= 1
    assert bool(snapshot.get("lock_locked")) is False

    await gateway.close()
