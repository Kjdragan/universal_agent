"""Tests for SessionBudget slot tracker."""

from __future__ import annotations

import pytest

from universal_agent.session_budget import SessionBudget


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the singleton between tests."""
    SessionBudget.reset_instance()
    yield
    SessionBudget.reset_instance()


# ---------------------------------------------------------------------------
# Basic acquire / release
# ---------------------------------------------------------------------------
def test_acquire_single_slot():
    budget = SessionBudget(max_slots=3)
    assert budget.available() == 3
    assert budget.acquire("simone.gateway") is True
    assert budget.available() == 2
    assert budget.used() == 1


def test_release_all_slots():
    budget = SessionBudget(max_slots=3)
    budget.acquire("simone.gateway", slots=2)
    assert budget.available() == 1
    budget.release("simone.gateway")
    assert budget.available() == 3


def test_release_partial_slots():
    budget = SessionBudget(max_slots=5)
    budget.acquire("cli.mission-1", slots=3)
    budget.release("cli.mission-1", slots=1)
    assert budget.used() == 2
    assert budget.available() == 3


def test_acquire_overflow_rejected():
    budget = SessionBudget(max_slots=2)
    assert budget.acquire("vp.coder", slots=2) is True
    assert budget.acquire("vp.general", slots=1) is False
    assert budget.available() == 0


def test_release_nonexistent_consumer_is_noop():
    budget = SessionBudget(max_slots=3)
    budget.release("ghost-consumer")  # Should not raise
    assert budget.available() == 3


# ---------------------------------------------------------------------------
# Multiple consumers
# ---------------------------------------------------------------------------
def test_multiple_consumers():
    budget = SessionBudget(max_slots=5)
    assert budget.acquire("simone.gateway", slots=1) is True
    assert budget.acquire("csi.analytics", slots=1) is True
    assert budget.acquire("cli.mission-1", slots=2) is True
    assert budget.available() == 1
    assert budget.used() == 4

    budget.release("csi.analytics")
    assert budget.available() == 2


# ---------------------------------------------------------------------------
# Heavy mode
# ---------------------------------------------------------------------------
def test_heavy_mode_lifecycle():
    budget = SessionBudget(max_slots=5)
    assert budget.heavy_mode_active is False

    assert budget.enter_heavy_mode("cli.mission-1") is True
    assert budget.heavy_mode_active is True

    # Second enter attempt should fail
    assert budget.enter_heavy_mode("cli.mission-2") is False

    budget.exit_heavy_mode("cli.mission-1")
    assert budget.heavy_mode_active is False

    # Now another consumer can enter
    assert budget.enter_heavy_mode("cli.mission-2") is True


def test_heavy_mode_auto_clears_on_release():
    budget = SessionBudget(max_slots=5)
    budget.acquire("cli.mission-1", slots=3)
    budget.enter_heavy_mode("cli.mission-1")
    assert budget.heavy_mode_active is True

    budget.release("cli.mission-1")
    assert budget.heavy_mode_active is False


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------
def test_status_returns_correct_shape():
    budget = SessionBudget(max_slots=5)
    budget.acquire("simone.gateway", metadata={"source": "gateway"})
    budget.acquire("cli.mission-1", slots=2, metadata={"type": "cli"})

    status = budget.status()
    assert status["max_slots"] == 5
    assert status["used_slots"] == 3
    assert status["available_slots"] == 2
    assert status["heavy_mode_active"] is False
    assert len(status["allocations"]) == 2

    ids = {a["consumer_id"] for a in status["allocations"]}
    assert ids == {"simone.gateway", "cli.mission-1"}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
def test_singleton_pattern():
    a = SessionBudget.get_instance(max_slots=4)
    b = SessionBudget.get_instance(max_slots=10)  # Should return same instance
    assert a is b
    assert a.max_slots == 4


# ---------------------------------------------------------------------------
# Stacking — acquire same consumer multiple times
# ---------------------------------------------------------------------------
def test_acquire_stacks_for_same_consumer():
    budget = SessionBudget(max_slots=5)
    budget.acquire("cli.mission-1", slots=1)
    budget.acquire("cli.mission-1", slots=2)
    assert budget.used() == 3
    budget.release("cli.mission-1")
    assert budget.used() == 0
