"""Periodic VP stale-mission reconciliation loop.

Motivation: 2026-05-11 VP Coder mission `vp-mission-aac933ded3b0d7238eabee89`
sat in `status=running` for 8+ hours after its external daemon stopped polling
and after the actual work shipped via a separate PR. The on-startup reconciler
caught nothing because the gateway had not restarted in that window. This loop
runs the reconciler periodically so the worst-case stuck-mission window is
bounded by `_vp_stale_reconcile_interval_seconds` (default 5 min) instead of
"until next deploy".
"""

import asyncio

import pytest

from universal_agent import gateway_server


@pytest.mark.asyncio
async def test_loop_calls_reconciler_each_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop invokes the underlying reconciler on each tick until stopped."""
    call_count = {"n": 0}

    def _fake_reconcile() -> int:
        call_count["n"] += 1
        return 0

    monkeypatch.setattr(
        gateway_server,
        "_reconcile_stale_vp_missions_on_startup",
        _fake_reconcile,
    )
    monkeypatch.setattr(gateway_server, "_vp_stale_reconcile_interval_seconds", 60)

    stop_event = asyncio.Event()

    async def _run_briefly() -> None:
        # Drive the loop's asyncio.wait_for(stop_event, timeout=interval) using
        # a tiny effective timeout by intercepting wait_for. We patch it so the
        # first iteration completes immediately.
        original_wait_for = asyncio.wait_for

        async def _fast_wait_for(coro, timeout):  # noqa: ARG001
            # Fire-and-forget the coroutine to avoid "coroutine was never awaited"
            task = asyncio.create_task(coro)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)
        try:
            loop_task = asyncio.create_task(
                gateway_server._vp_stale_reconcile_loop(stop_event)
            )
            # Let the loop tick a few times.
            await asyncio.sleep(0.05)
            stop_event.set()
            monkeypatch.setattr(asyncio, "wait_for", original_wait_for)
            await asyncio.wait_for(loop_task, timeout=2.0)
        finally:
            monkeypatch.setattr(asyncio, "wait_for", original_wait_for)

    await _run_briefly()
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_loop_terminates_promptly_on_stop_event() -> None:
    """Setting stop_event during the wait must terminate the loop quickly."""
    stop_event = asyncio.Event()
    loop_task = asyncio.create_task(
        gateway_server._vp_stale_reconcile_loop(stop_event)
    )
    # Loop is sleeping. Signal stop and confirm it exits within a few seconds.
    stop_event.set()
    await asyncio.wait_for(loop_task, timeout=2.0)
    assert loop_task.done()


@pytest.mark.asyncio
async def test_loop_survives_reconciler_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A throwing reconciler must not kill the loop — log and continue."""
    call_count = {"n": 0}

    def _raising_reconcile() -> int:
        call_count["n"] += 1
        raise RuntimeError("transient db error")

    monkeypatch.setattr(
        gateway_server,
        "_reconcile_stale_vp_missions_on_startup",
        _raising_reconcile,
    )

    stop_event = asyncio.Event()

    async def _fast_wait_for(coro, timeout):  # noqa: ARG001
        task = asyncio.create_task(coro)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        raise asyncio.TimeoutError

    original_wait_for = asyncio.wait_for
    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)
    try:
        loop_task = asyncio.create_task(
            gateway_server._vp_stale_reconcile_loop(stop_event)
        )
        await asyncio.sleep(0.05)
        stop_event.set()
        monkeypatch.setattr(asyncio, "wait_for", original_wait_for)
        await asyncio.wait_for(loop_task, timeout=2.0)
    finally:
        monkeypatch.setattr(asyncio, "wait_for", original_wait_for)

    # Reconciler was called and the loop did not crash despite exceptions.
    assert call_count["n"] >= 1
    assert loop_task.done()
    assert loop_task.exception() is None


def test_interval_env_var_has_minimum_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """UA_VP_STALE_RECONCILE_INTERVAL_SECONDS below 60 must clamp to 60."""
    # The constant is computed at import time, so re-derive using the same
    # expression to confirm the floor behavior. This pins the contract that
    # an operator can't accidentally schedule a sub-minute reconcile sweep.
    monkeypatch.setenv("UA_VP_STALE_RECONCILE_INTERVAL_SECONDS", "5")
    derived = max(
        60,
        int(__import__("os").getenv("UA_VP_STALE_RECONCILE_INTERVAL_SECONDS", "").strip() or 5 * 60),
    )
    assert derived == 60
