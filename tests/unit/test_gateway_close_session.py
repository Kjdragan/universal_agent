"""Guards for the consolidated InProcessGateway.close_session (2026-06-16).

close_session was defined twice; the dead variant called a non-existent
adapter.teardown() and the live one leaked _session_exec_locks. These pin the
single consolidated behavior: pop _adapters + _sessions + _session_exec_locks,
await adapter.close(), no-op for unknown sessions, swallow a close() error, and a
regression guard that exactly one definition exists.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from universal_agent.gateway import InProcessGateway


class _FakeAdapter:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _BoomAdapter:
    async def close(self):
        raise RuntimeError("boom")


def _gw():
    gw = InProcessGateway.__new__(InProcessGateway)
    gw._adapters = {}
    gw._sessions = {}
    gw._session_exec_locks = {}
    return gw


@pytest.mark.asyncio
async def test_close_session_pops_all_three_and_closes_adapter():
    gw = _gw()
    adapter = _FakeAdapter()
    gw._adapters["s1"] = adapter
    gw._sessions["s1"] = object()
    gw._session_exec_locks["s1"] = asyncio.Lock()

    await gw.close_session("s1")

    assert adapter.closed is True
    assert "s1" not in gw._adapters
    assert "s1" not in gw._sessions
    # The leak the consolidation fixes (the live variant never popped this).
    assert "s1" not in gw._session_exec_locks


@pytest.mark.asyncio
async def test_close_session_unknown_session_is_noop():
    gw = _gw()
    await gw.close_session("nope")  # must not raise


@pytest.mark.asyncio
async def test_close_session_swallows_adapter_close_error():
    gw = _gw()
    gw._adapters["s1"] = _BoomAdapter()
    gw._sessions["s1"] = object()
    gw._session_exec_locks["s1"] = asyncio.Lock()

    await gw.close_session("s1")  # a failing adapter.close() must not propagate

    assert "s1" not in gw._adapters
    assert "s1" not in gw._sessions
    assert "s1" not in gw._session_exec_locks


def test_close_session_defined_exactly_once():
    # Regression guard: the duplicate definition must not come back.
    src = inspect.getsource(InProcessGateway)
    assert src.count("async def close_session") == 1
