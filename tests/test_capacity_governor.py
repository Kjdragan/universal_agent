"""Tests for the Capacity Governor — system-level rate limiting."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from universal_agent.services.capacity_governor import (
    CapacityGovernor,
    check_capacity,
    capacity_snapshot,
)


@pytest.fixture(autouse=True)
def _reset_governor():
    """Reset the singleton between tests."""
    CapacityGovernor.reset_instance()
    yield
    CapacityGovernor.reset_instance()


# ── Basic lifecycle ──────────────────────────────────────────────────────────

class TestCapacityGovernorBasic:
    def test_singleton_creation(self):
        g1 = CapacityGovernor.get_instance(max_concurrent=3)
        g2 = CapacityGovernor.get_instance()
        assert g1 is g2
        assert g1._max_concurrent == 3

    def test_default_allows_dispatch(self):
        g = CapacityGovernor.get_instance(max_concurrent=2)
        ok, reason = g.can_dispatch()
        assert ok is True
        assert "available" in reason

    def test_snapshot_initial_state(self):
        g = CapacityGovernor.get_instance(max_concurrent=2)
        snap = g.snapshot()
        assert snap.max_concurrent == 2
        assert snap.active_slots == 0
        assert snap.available_slots == 2
        assert snap.in_backoff is False
        assert snap.total_429s == 0
        assert snap.total_shed == 0

    def test_snapshot_dict(self):
        g = CapacityGovernor.get_instance(max_concurrent=2)
        d = g.snapshot_dict()
        assert isinstance(d, dict)
        assert d["max_concurrent"] == 2
        assert d["in_backoff"] is False


# ── Slot acquisition ─────────────────────────────────────────────────────────

class TestCapacitySlots:
    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        g = CapacityGovernor.get_instance(max_concurrent=2)
        async with g.acquire_slot("test"):
            snap = g.snapshot()
            assert snap.active_slots == 1
            assert snap.available_slots == 1
        # After release
        snap = g.snapshot()
        assert snap.active_slots == 0
        assert snap.available_slots == 2

    @pytest.mark.asyncio
    async def test_concurrent_slot_limiting(self):
        g = CapacityGovernor.get_instance(max_concurrent=1)

        # Acquire the only slot
        async with g.acquire_slot("first"):
            # Now at capacity
            ok, reason = g.can_dispatch()
            assert ok is False
            assert "capacity_full" in reason

        # After release, capacity should be available
        ok, reason = g.can_dispatch()
        assert ok is True

    @pytest.mark.asyncio
    async def test_slot_timeout_on_full_capacity(self):
        g = CapacityGovernor.get_instance(max_concurrent=1)

        async with g.acquire_slot("blocker"):
            # Try to acquire second slot — should timeout
            with pytest.raises(asyncio.TimeoutError):
                async with g.acquire_slot("blocked"):
                    pass

        # Verify shed counter incremented
        snap = g.snapshot()
        assert snap.total_shed >= 1


# ── 429 backoff ──────────────────────────────────────────────────────────────

class TestCapacityBackoff:
    @pytest.mark.asyncio
    async def test_single_429_triggers_backoff(self):
        g = CapacityGovernor.get_instance(
            max_concurrent=2,
            backoff_base=10.0,
            cooldown_after_429=10.0,
        )
        backoff = await g.report_rate_limit("test_caller")
        assert backoff >= 10.0

        ok, reason = g.can_dispatch()
        assert ok is False
        assert "backoff" in reason

        snap = g.snapshot()
        assert snap.in_backoff is True
        assert snap.total_429s == 1
        assert snap.consecutive_429s == 1

    @pytest.mark.asyncio
    async def test_consecutive_429s_increase_backoff(self):
        g = CapacityGovernor.get_instance(
            max_concurrent=2,
            backoff_base=5.0,
            backoff_max=120.0,
            cooldown_after_429=5.0,
        )
        b1 = await g.report_rate_limit("caller1")
        # Reset backoff_until so we can trigger another
        g._backoff_until = 0
        b2 = await g.report_rate_limit("caller2")
        # Second should be larger due to exponential backoff
        assert b2 > b1
        assert g._consecutive_429s == 2

    @pytest.mark.asyncio
    async def test_success_clears_backoff(self):
        g = CapacityGovernor.get_instance(
            max_concurrent=2,
            backoff_base=5.0,
            cooldown_after_429=5.0,
        )
        await g.report_rate_limit("test")
        assert g.snapshot().in_backoff is True

        # Report success — should clear
        g._consecutive_429s = 1  # Need at least 1 to trigger count
        await g.report_success("test")
        # consecutive should be 0 now, backoff cleared
        assert g._consecutive_429s == 0
        assert g._backoff_until == 0.0

    @pytest.mark.asyncio
    async def test_backoff_expires_naturally(self):
        g = CapacityGovernor.get_instance(
            max_concurrent=2,
            backoff_base=0.1,
            cooldown_after_429=0.1,
        )
        await g.report_rate_limit("test")
        # Wait for backoff to expire
        await asyncio.sleep(0.3)
        ok, reason = g.can_dispatch()
        assert ok is True


# ── Shed tracking ────────────────────────────────────────────────────────────

class TestCapacityShedding:
    @pytest.mark.asyncio
    async def test_shed_counter_on_backoff_denial(self):
        g = CapacityGovernor.get_instance(
            max_concurrent=2,
            cooldown_after_429=60.0,
        )
        await g.report_rate_limit("trigger")
        # Now in backoff — each can_dispatch should increment shed
        ok, _ = g.can_dispatch()
        assert ok is False
        ok, _ = g.can_dispatch()
        assert ok is False
        assert g.snapshot().total_shed == 2


# ── Module-level convenience ─────────────────────────────────────────────────

class TestModuleFunctions:
    def test_check_capacity_convenience(self):
        CapacityGovernor.get_instance(max_concurrent=2)
        ok, reason = check_capacity()
        assert ok is True

    def test_capacity_snapshot_convenience(self):
        CapacityGovernor.get_instance(max_concurrent=2)
        snap = capacity_snapshot()
        assert isinstance(snap, dict)
        assert snap["max_concurrent"] == 2
"""End of capacity governor tests."""
