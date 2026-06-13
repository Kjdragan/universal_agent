"""Unit tests for the bounded in-flight drain helper (deploy-restart resilience C2).

Behavior under test: ``drain_inflight`` lets an in-flight awaitable run to
completion within a bounded budget, classifies the outcome, and — crucially —
does NOT itself cancel the work on timeout (the caller owns cancellation, so it
can reuse the existing teardown path). See
``project_docs/06_platform/12_deploy_restart_resilience_adr.md`` §C2.
"""

import asyncio

from universal_agent.graceful_drain import DrainResult, drain_inflight


async def test_nothing_in_flight_returns_immediately():
    outcome = await drain_inflight(None, timeout=5.0)
    assert outcome.result is DrainResult.NOTHING_IN_FLIGHT
    assert outcome.waited_seconds < 0.5


async def test_work_that_finishes_in_time_is_drained():
    done = {"ran": False}

    async def work():
        await asyncio.sleep(0.05)
        done["ran"] = True

    outcome = await drain_inflight(asyncio.ensure_future(work()), timeout=2.0)
    assert outcome.result is DrainResult.DRAINED
    assert done["ran"] is True


async def test_work_exceeding_budget_times_out_and_is_not_cancelled():
    """Core safety: on timeout the helper leaves the work running — the caller
    owns cancellation. This is what lets the heartbeat fall back to its existing
    cancel path only after granting the budget."""
    finished = {"ran": False}

    async def slow_work():
        try:
            await asyncio.sleep(5.0)
            finished["ran"] = True
        except asyncio.CancelledError:
            finished["ran"] = False
            raise

    task = asyncio.ensure_future(slow_work())
    outcome = await drain_inflight(task, timeout=0.1)
    assert outcome.result is DrainResult.TIMED_OUT
    assert not task.cancelled()  # helper did NOT cancel it
    assert not task.done()  # still running
    # cleanup
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_work_that_raises_counts_as_drained():
    async def failing_work():
        await asyncio.sleep(0.02)
        raise RuntimeError("iteration blew up")

    outcome = await drain_inflight(asyncio.ensure_future(failing_work()), timeout=2.0)
    assert outcome.result is DrainResult.DRAINED


async def test_already_done_future_needs_no_wait():
    async def quick():
        return 1

    fut = asyncio.ensure_future(quick())
    await fut  # already complete
    outcome = await drain_inflight(fut, timeout=5.0)
    assert outcome.result is DrainResult.NOTHING_IN_FLIGHT
    assert outcome.waited_seconds == 0.0
