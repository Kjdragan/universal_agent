"""Tests for the single-flight TTL cache on ``/api/v1/dashboard/summary``.

Regression introduced 2026-05-28: the endpoint stalled at 15s+ under
dashboard polling pressure because every poll did a fresh scan of the 1.4GB
activity_state.db. Even though the compute was wrapped in ``asyncio.to_thread``,
N parallel polls saturated the thread pool. Pin two behaviors:

1. Two cache hits within the TTL share one compute (single-flight).
2. After the TTL elapses, a fresh compute fires.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def reset_summary_cache():
    """Clear the module-level cache before each test."""
    import universal_agent.gateway_server as gs

    gs._dashboard_summary_cache["data"] = None
    gs._dashboard_summary_cache["computed_at"] = 0.0
    yield
    gs._dashboard_summary_cache["data"] = None
    gs._dashboard_summary_cache["computed_at"] = 0.0


def test_cache_hit_within_ttl_skips_compute():
    import universal_agent.gateway_server as gs

    call_count = {"n": 0}

    def _slow_compute() -> dict[str, Any]:
        call_count["n"] += 1
        return {"call": call_count["n"], "totals": {"unread": 1}}

    with patch.object(gs, "_dashboard_summary_sync_compute", _slow_compute):
        first = gs._dashboard_summary_cached()
        second = gs._dashboard_summary_cached()
        third = gs._dashboard_summary_cached()

    assert call_count["n"] == 1, "second and third calls must hit cache"
    assert first is second
    assert second is third


def test_cache_recomputes_after_ttl_expires(monkeypatch):
    import universal_agent.gateway_server as gs

    monkeypatch.setattr(gs, "_DASHBOARD_SUMMARY_CACHE_TTL_SECONDS", 0.05)

    call_count = {"n": 0}

    def _fast_compute() -> dict[str, Any]:
        call_count["n"] += 1
        return {"call": call_count["n"]}

    with patch.object(gs, "_dashboard_summary_sync_compute", _fast_compute):
        gs._dashboard_summary_cached()
        # Sleep past the TTL.
        time.sleep(0.08)
        gs._dashboard_summary_cached()

    assert call_count["n"] == 2


def test_concurrent_callers_share_one_compute():
    """Under stampede, the lock must collapse N parallel callers into 1 compute.

    Without the cache, the gateway saturated its to_thread pool and stalled at
    15s when several browser tabs polled in parallel.
    """
    import universal_agent.gateway_server as gs

    compute_started = threading.Event()
    release_compute = threading.Event()
    call_count = {"n": 0}

    def _gated_compute() -> dict[str, Any]:
        call_count["n"] += 1
        compute_started.set()
        # Block until the test releases us, simulating a slow query.
        release_compute.wait(timeout=2.0)
        return {"call": call_count["n"]}

    results: list[dict[str, Any]] = []
    threads: list[threading.Thread] = []

    def _worker():
        results.append(gs._dashboard_summary_cached())

    with patch.object(gs, "_dashboard_summary_sync_compute", _gated_compute):
        # Spawn 10 parallel callers.
        for _ in range(10):
            t = threading.Thread(target=_worker)
            threads.append(t)
            t.start()
        # Wait until the first compute is in flight, then let it finish.
        assert compute_started.wait(timeout=1.0), "first compute never started"
        release_compute.set()
        for t in threads:
            t.join(timeout=2.0)
            assert not t.is_alive(), "a caller hung past the deadline"

    assert call_count["n"] == 1, f"expected single-flight, got {call_count['n']} computes"
    assert len(results) == 10
    # All callers must see the same payload.
    assert all(r == results[0] for r in results)


def test_ttl_zero_disables_cache(monkeypatch):
    """``UA_DASHBOARD_SUMMARY_CACHE_TTL_SECONDS=0`` forces every call to recompute."""
    import universal_agent.gateway_server as gs

    monkeypatch.setattr(gs, "_DASHBOARD_SUMMARY_CACHE_TTL_SECONDS", 0.0)

    call_count = {"n": 0}

    def _compute() -> dict[str, Any]:
        call_count["n"] += 1
        return {"call": call_count["n"]}

    with patch.object(gs, "_dashboard_summary_sync_compute", _compute):
        gs._dashboard_summary_cached()
        gs._dashboard_summary_cached()
        gs._dashboard_summary_cached()

    assert call_count["n"] == 3
