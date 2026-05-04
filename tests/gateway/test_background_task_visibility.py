"""Background-task error visibility (the heartbeat-class fix).

The 2026-05-01 heartbeat silence went undetected for ~26 hours because
`_spawn_background_task` created tasks with `loop.create_task(coro)`
and never attached an error callback.  Any exception raised inside a
spawned task — heartbeat startup, ToDo dispatcher, gws event listener,
agent-mail bootstrap, etc. — vanished into the asyncio loop with no
operator-visible signal.

These tests pin the contract that:

  1. A background task that raises a "real" exception emits a
     `background_task_failed` notification, severity error, with the
     task_name in metadata.
  2. A graceful shutdown raising `asyncio.CancelledError` does NOT
     emit a notification — long-lived loops legitimately raise it on
     deploy/restart and we must not cry wolf.
  3. `_run_after_deployment_window` failures emit a
     `service_startup_failed` notification with the component name
     (and do not double-fire `background_task_failed`).
"""

import asyncio
from types import SimpleNamespace

import pytest

from universal_agent import gateway_server


@pytest.fixture(autouse=True)
def _isolate_notification_state(monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])


@pytest.mark.asyncio
async def test_background_task_failure_surfaces_notification():
    """A task that raises a normal exception must emit one
    `background_task_failed` notification with the task_name in
    metadata."""

    async def _raises():
        await asyncio.sleep(0)
        raise RuntimeError("boom from a background coroutine")

    gateway_server._spawn_background_task(_raises(), task_name="unit_test_raiser")

    # Yield to let the task run + the done-callback fire.
    for _ in range(10):
        await asyncio.sleep(0)

    failed = [
        n for n in gateway_server._notifications
        if str(n.get("kind") or "").lower() == "background_task_failed"
    ]
    assert len(failed) == 1, (
        f"Expected exactly one background_task_failed notification; "
        f"got {len(failed)} (notifications: {gateway_server._notifications})."
    )
    notif = failed[0]
    assert notif["severity"] == "error"
    assert notif["metadata"]["task_name"] == "unit_test_raiser"
    assert "boom from a background coroutine" in str(notif["metadata"].get("error") or "")
    assert notif["metadata"]["exception_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_background_task_cancelled_does_not_notify():
    """Long-lived background loops (heartbeat tick loop, gws listener,
    YouTube playlist watcher, etc.) raise `CancelledError` on graceful
    shutdown.  Those are operationally normal and must NOT generate
    a `background_task_failed` alert — that would paint the dashboard
    red on every deploy."""

    async def _cancellable():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    gateway_server._spawn_background_task(_cancellable(), task_name="unit_test_cancelled")

    # Find the task we just spawned so we can cancel it.  We can't easily
    # introspect the asyncio.Task list portably, so we just sleep enough
    # to ensure the task is registered, then cancel via all_tasks().
    await asyncio.sleep(0)
    for task in asyncio.all_tasks():
        coro = getattr(task, "get_coro", lambda: None)()
        if coro is not None and getattr(coro, "__name__", "") == "_cancellable":
            task.cancel()
    for _ in range(10):
        await asyncio.sleep(0)

    failed = [
        n for n in gateway_server._notifications
        if str(n.get("kind") or "").lower() == "background_task_failed"
    ]
    assert failed == [], (
        f"CancelledError must not produce a background_task_failed alert; got {failed}."
    )


@pytest.mark.asyncio
async def test_run_after_deployment_window_failure_emits_service_startup_failed(monkeypatch):
    """If a service's startup coroutine raises after the deployment
    window closes, `_run_after_deployment_window` must emit a
    `service_startup_failed` notification with the component name —
    this is the architectural hole that allowed the 2026-05-01
    heartbeat silence to go undetected for 26 hours."""

    # Bypass the actual deployment-window wait helper so the test
    # doesn't depend on filesystem state.
    async def _no_wait(component: str) -> None:
        return None

    monkeypatch.setattr(gateway_server, "_wait_for_deployment_window_to_close", _no_wait)

    async def _faulty_startup():
        raise ConnectionError("redis unreachable during startup")

    # Run the wrapper directly (not via _spawn_background_task) so we
    # can control the assertion timing.
    await gateway_server._run_after_deployment_window(
        "heartbeat_service",
        _faulty_startup,
    )

    startup_failed = [
        n for n in gateway_server._notifications
        if str(n.get("kind") or "").lower() == "service_startup_failed"
    ]
    assert len(startup_failed) == 1, (
        "Service startup failure must produce exactly one service_startup_failed alert."
    )
    notif = startup_failed[0]
    assert notif["severity"] == "error"
    assert notif["metadata"]["component"] == "heartbeat_service"
    assert "redis unreachable" in str(notif["metadata"].get("error") or "")

    # And no double-fire as background_task_failed — the deployment
    # window helper swallows the exception after emitting the specific
    # notification, so the outer task callback (when used) sees a
    # cleanly-completed task.
    background_failed = [
        n for n in gateway_server._notifications
        if str(n.get("kind") or "").lower() == "background_task_failed"
    ]
    assert background_failed == [], (
        "service_startup_failed must not double-fire as background_task_failed."
    )
