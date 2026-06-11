"""Loop-resilience tests for ZAIRateLimiter.

The limiter is a process-global singleton, but its asyncio primitives can
only serve one event loop (CPython 3.10+ primitives bind — lazily, on first
CONTENDED use — to a loop; contended use from another loop then raises
``RuntimeError: ... is bound to a different event loop``). Two real UA
patterns put the singleton on multiple loops:

1. SEQUENTIAL loops — the convergence subprocess
   (`scripts/csi_convergence_sync.py`) drives each LLM call through
   sync→async bridges (`proactive_convergence.py::_detect_clusters_llm` and
   friends) that each run a fresh ``asyncio.run()`` loop.
2. CONCURRENT loops — the gateway can run sync background work on a
   Starlette threadpool thread, which itself calls ``asyncio.run()`` while
   the gateway's main loop keeps serving and using the limiter.

The fix serves a per-loop primitives bundle
(`rate_limiter.py::_LoopPrimitives` via
`ZAIRateLimiter._get_loop_primitives`) and keeps shared adaptive state
behind a ``threading.Lock``. NOTE the contention nuance: the uncontended
fast path of ``Semaphore.acquire``/``Lock.acquire`` never binds, so crash
repros must drive more concurrent acquires than the cap.

THE pre-fix repro is ``test_acquire_survives_successive_asyncio_run_loops``
(empirically fails on the pre-fix limiter with the RuntimeError above).
The other tests are post-fix behavior pins: state carry-over, concurrent
cross-thread loops, cap enforcement, bundle bookkeeping.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from universal_agent.rate_limiter import ZAIRateLimiter


@pytest.fixture
def isolated_limiter(tmp_path, monkeypatch):
    """Fresh singleton with fast spacing and an isolated snapshot path."""
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(tmp_path / "zai_state.json"))
    monkeypatch.setenv("ZAI_MIN_INTERVAL", "0.01")
    monkeypatch.setenv("ZAI_MAX_CONCURRENT", "2")
    ZAIRateLimiter.reset_instance()
    yield ZAIRateLimiter.get_instance()
    ZAIRateLimiter.reset_instance()


def _contended_acquires(limiter: ZAIRateLimiter, n: int = 3):
    """Run n concurrent acquires (n > cap) so semaphore waiters are created
    and the primitives loop-bind. Returns a coroutine for asyncio.run()."""

    async def one() -> None:
        async with limiter.acquire("loop-resilience-test"):
            await asyncio.sleep(0.01)

    async def fan_out() -> None:
        await asyncio.gather(*(one() for _ in range(n)))

    return fan_out()


def test_acquire_survives_successive_asyncio_run_loops(isolated_limiter):
    """THE repro — pins the original crash. On the pre-fix limiter (single
    shared semaphore) the second asyncio.run() loop's contended acquire
    raised RuntimeError('... is bound to a different event loop')."""
    asyncio.run(_contended_acquires(isolated_limiter))
    asyncio.run(_contended_acquires(isolated_limiter))  # raised before the fix


def test_concurrent_loops_in_two_threads(isolated_limiter):
    """B1 regression gate (required before UA_LLM_CLASSIFIER_LIMITER_ENABLED
    may be flipped on in prod): a second LIVE loop on another thread uses
    the limiter while the first loop is mid-flight. Must not raise, must
    keep enforcing the cap within each loop, and must count the conflict.

    This is the gateway pattern: main loop serving while a Starlette
    threadpool thread runs asyncio.run() convergence work.
    """
    limiter = isolated_limiter
    started = threading.Event()
    release_main = threading.Event()
    errors: list[BaseException] = []

    async def main_loop_work() -> None:
        # Hold a slot across the other thread's whole lifetime, with real
        # contention so the main loop's primitives are loop-bound.
        async def hold() -> None:
            async with limiter.acquire("main-loop"):
                started.set()
                while not release_main.is_set():
                    await asyncio.sleep(0.01)

        async def churn() -> None:
            for _ in range(3):
                async with limiter.acquire("main-loop-churn"):
                    await asyncio.sleep(0.01)

        await asyncio.gather(hold(), churn())

    def thread_loop_work() -> None:
        try:
            started.wait(timeout=5)
            for _ in range(2):  # successive loops, each contended
                asyncio.run(_contended_acquires(limiter))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            release_main.set()

    t = threading.Thread(target=thread_loop_work)
    t.start()
    asyncio.run(main_loop_work())
    t.join(timeout=10)
    assert not t.is_alive()
    assert errors == []
    # The overlap was detected and counted (visible to the watchdog).
    assert limiter._cross_loop_conflicts >= 1
    # And persisted: any record_* write carries the counter to the snapshot.
    asyncio.run(limiter.record_success())
    from universal_agent.rate_limiter import _get_state_path

    snapshot = json.loads(_get_state_path().read_text())
    assert snapshot["cross_loop_conflicts"] >= 1


def test_counters_carry_over_across_loops(isolated_limiter):
    """Post-fix behavior pin: per-loop bundles must NOT reset adaptive
    state — totals, backoff floor, and 429 streaks span loops."""

    async def burst_429s() -> None:
        for _ in range(3):  # sequential: keeps the within-10s streak logic deterministic
            await isolated_limiter.record_429("carry-over-test")

    asyncio.run(burst_429s())
    stats_loop1 = isolated_limiter.get_stats()
    assert stats_loop1["total_429s"] == 3
    assert stats_loop1["consecutive_429s"] == 3
    floor_loop1 = stats_loop1["backoff_floor"]
    assert floor_loop1 > 1.0  # ramped above the initial floor

    async def one_more_429_after_acquire() -> None:
        async def hold() -> None:
            async with isolated_limiter.acquire("carry-over-test"):
                await asyncio.sleep(0.01)

        await asyncio.gather(hold(), hold(), hold())
        await isolated_limiter.record_429("carry-over-test")

    asyncio.run(one_more_429_after_acquire())
    stats_loop2 = isolated_limiter.get_stats()
    assert stats_loop2["total_429s"] == 4
    assert stats_loop2["consecutive_429s"] == 4
    assert stats_loop2["backoff_floor"] >= floor_loop1


def test_record_methods_are_loop_and_thread_agnostic(isolated_limiter):
    """Post-fix behavior pin. record_* critical sections are synchronous
    (a threading.Lock, no awaits), so they cannot loop-bind at all — and
    they must stay correct when called from loops on different threads
    concurrently."""
    limiter = isolated_limiter
    errors: list[BaseException] = []

    def run_records() -> None:
        try:
            async def batch() -> None:
                for _ in range(10):
                    await limiter.record_429("thread-test")
                    await limiter.record_success()

            asyncio.run(batch())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run_records) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    stats = limiter.get_stats()
    assert stats["total_429s"] == 30
    assert stats["total_requests"] == 30


def test_closed_loops_are_pruned(isolated_limiter):
    """One-shot asyncio.run() loops must not leak bundles (the convergence
    subprocess creates one loop per LLM call)."""
    for _ in range(5):
        asyncio.run(_contended_acquires(isolated_limiter))
    # All previous loops are closed; at most the latest bundle may remain
    # until the next creation prunes it.
    assert len(isolated_limiter._loop_primitives) <= 1
    # Sequential loop replacement is NOT a cross-loop conflict.
    assert isolated_limiter._cross_loop_conflicts == 0


def test_single_loop_behavior_unchanged(isolated_limiter):
    """In the common case (one persistent loop, e.g. the gateway) concurrent
    acquires still enforce the cap and complete cleanly."""
    peak = 0
    active = 0

    async def one() -> None:
        nonlocal peak, active
        async with isolated_limiter.acquire("single-loop-test"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    async def fan_out() -> None:
        await asyncio.gather(*(one() for _ in range(5)))

    asyncio.run(fan_out())
    assert peak <= 2  # ZAI_MAX_CONCURRENT=2 enforced throughout
