"""
Phase 6 — Concurrency Stress Tests

Verifies that the per-session execution lock allows different sessions to run
concurrently while correctly serializing turns within the same session.  Also
validates that SessionContext ContextVar state is fully isolated between
concurrent asyncio tasks (no cross-session contamination).

These tests do NOT hit the real LLM.  They use mock adapters that simulate
work with asyncio.sleep() so concurrency is observable via wall-clock timing.
"""
import asyncio
import time
from pathlib import Path
from typing import AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent.session_ctx import (
    SessionContext,
    get_ctx,
    require_ctx,
    set_ctx,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _run_isolated_session(
    run_id: str,
    duration: float,
    results: dict,
) -> None:
    """
    Simulate a complete process_turn session:
    1. Set ContextVar for this asyncio task.
    2. Do async work (sleep to simulate LLM).
    3. Capture ctx state at the end.
    4. Verify ctx was not mutated by sibling tasks.
    """
    ctx = SessionContext(run_id=run_id, current_step_id=f"step-{run_id}")
    set_ctx(ctx)

    start = time.monotonic()
    await asyncio.sleep(duration)
    elapsed = time.monotonic() - start

    # Capture final state
    final_ctx = require_ctx()
    results[run_id] = {
        "run_id": final_ctx.run_id,
        "step_id": final_ctx.current_step_id,
        "elapsed": elapsed,
    }
    # Mutate ctx to prove sibling writes don't bleed through
    final_ctx.current_step_id = f"done-{run_id}"


# ---------------------------------------------------------------------------
# ContextVar isolation under concurrent tasks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_concurrent_sessions_isolated_run_id():
    """Two simultaneous sessions must each see their own run_id."""
    results: dict = {}
    await asyncio.gather(
        _run_isolated_session("run-alpha", 0.05, results),
        _run_isolated_session("run-beta", 0.05, results),
    )
    assert results["run-alpha"]["run_id"] == "run-alpha"
    assert results["run-beta"]["run_id"] == "run-beta"


@pytest.mark.asyncio
async def test_concurrent_step_id_mutation_does_not_bleed():
    """Step-ID mutation in one task must not affect another."""
    results: dict = {}
    await asyncio.gather(
        _run_isolated_session("run-x", 0.04, results),
        _run_isolated_session("run-y", 0.04, results),
    )
    # Each task mutates to "done-<run_id>" — verify no cross-mutation
    assert results["run-x"]["step_id"] == "step-run-x"
    assert results["run-y"]["step_id"] == "step-run-y"


@pytest.mark.asyncio
async def test_five_concurrent_sessions_all_isolated():
    """Five sessions running at once — every session sees its own context."""
    results: dict = {}
    run_ids = [f"run-{i}" for i in range(5)]
    await asyncio.gather(
        *[_run_isolated_session(rid, 0.03, results) for rid in run_ids]
    )
    for rid in run_ids:
        assert results[rid]["run_id"] == rid, f"run_id mismatch for {rid}"
        assert results[rid]["step_id"] == f"step-{rid}", f"step_id mismatch for {rid}"


# ---------------------------------------------------------------------------
# Gateway per-session lock: different sessions run concurrently
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_sessions_have_independent_locks():
    """_get_session_exec_lock returns different Lock objects for different sessions."""
    from universal_agent.gateway import InProcessGateway
    gw = InProcessGateway.__new__(InProcessGateway)
    gw._session_exec_locks = {}

    lock_a = gw._get_session_exec_lock("sess-a")
    lock_b = gw._get_session_exec_lock("sess-b")

    assert lock_a is not lock_b, "Different sessions must have distinct locks"
    assert gw._get_session_exec_lock("sess-a") is lock_a, "Same session must reuse the same lock"
    assert gw._get_session_exec_lock("sess-b") is lock_b


@pytest.mark.asyncio
async def test_different_sessions_execute_concurrently():
    """
    Two tasks for different sessions acquire their respective per-session locks
    and run concurrently — total wall-clock time is ~SLEEP_S, not 2×SLEEP_S.
    """
    from universal_agent.gateway import InProcessGateway

    SLEEP_S = 0.12
    CONCURRENCY_THRESHOLD = SLEEP_S * 1.7

    gw = InProcessGateway.__new__(InProcessGateway)
    gw._session_exec_locks = {}

    results: list[str] = []

    async def _simulate_session(session_id: str) -> None:
        lock = gw._get_session_exec_lock(session_id)
        async with lock:
            await asyncio.sleep(SLEEP_S)
            results.append(session_id)

    t0 = time.monotonic()
    await asyncio.gather(
        _simulate_session("sess-alpha"),
        _simulate_session("sess-beta"),
    )
    wall = time.monotonic() - t0

    assert wall < CONCURRENCY_THRESHOLD, (
        f"Concurrent sessions took {wall:.3f}s >= {CONCURRENCY_THRESHOLD:.3f}s "
        f"(expected ~{SLEEP_S:.2f}s — sessions appear to be serialized)"
    )
    assert set(results) == {"sess-alpha", "sess-beta"}


@pytest.mark.asyncio
async def test_same_session_turns_serialized():
    """
    Two tasks for the SAME session must serialize behind their shared lock,
    so total wall-clock time is >= 2 × SLEEP_S.
    """
    from universal_agent.gateway import InProcessGateway

    SLEEP_S = 0.08

    gw = InProcessGateway.__new__(InProcessGateway)
    gw._session_exec_locks = {}

    execution_order: list[int] = []

    async def _turn(idx: int) -> None:
        lock = gw._get_session_exec_lock("shared-session")
        async with lock:
            await asyncio.sleep(SLEEP_S)
            execution_order.append(idx)

    t0 = time.monotonic()
    await asyncio.gather(_turn(1), _turn(2))
    wall = time.monotonic() - t0

    assert wall >= SLEEP_S * 1.8, (
        f"Same-session turns took {wall:.3f}s — expected serialized >= {SLEEP_S * 1.8:.3f}s"
    )
    assert len(execution_order) == 2, "Both turns must complete"
