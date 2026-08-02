"""Tests for universal_agent.services.proactive_budget — daily budget tracking."""

from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from universal_agent.services.proactive_budget import (
    DEFAULT_DAILY_BUDGET,
    _parse_int_env,
    get_budget_remaining,
    get_daily_proactive_count,
    get_ideation_ticks,
    has_daily_budget,
    has_ideation_tick_budget,
    increment_daily_proactive_count,
    increment_ideation_ticks,
    should_ideate_now,
)


def _houston_epoch(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    """Epoch seconds for a Houston (America/Chicago) local wall-clock time."""
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/Chicago")).timestamp()


@pytest.fixture
def conn(tmp_path):
    """In-memory DB with task_hub schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    from universal_agent import task_hub
    task_hub.ensure_schema(db)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# _parse_int_env
# ---------------------------------------------------------------------------


class TestParseIntEnv:
    def test_valid_integer(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "42")
        assert _parse_int_env("TEST_KEY", 0) == 42

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        assert _parse_int_env("MISSING_KEY", 99) == 99

    def test_empty_string_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "")
        assert _parse_int_env("TEST_KEY", 7) == 7

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "   ")
        assert _parse_int_env("TEST_KEY", 7) == 7

    def test_non_numeric_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "not_a_number")
        assert _parse_int_env("TEST_KEY", 5) == 5

    def test_negative_value_parsed(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "-3")
        assert _parse_int_env("TEST_KEY", 0) == -3

    def test_float_string_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "3.14")
        assert _parse_int_env("TEST_KEY", 10) == 10


# ---------------------------------------------------------------------------
# get_daily_proactive_count
# ---------------------------------------------------------------------------


class TestGetDailyProactiveCount:
    def test_starts_at_zero(self, conn):
        assert get_daily_proactive_count(conn) == 0

    def test_reflects_increment(self, conn):
        increment_daily_proactive_count(conn)
        increment_daily_proactive_count(conn, increment=3)
        assert get_daily_proactive_count(conn) == 4


# ---------------------------------------------------------------------------
# increment_daily_proactive_count
# ---------------------------------------------------------------------------


class TestIncrementDailyProactiveCount:
    def test_starts_from_one(self, conn):
        result = increment_daily_proactive_count(conn)
        assert result == 1

    def test_custom_increment(self, conn):
        result = increment_daily_proactive_count(conn, increment=5)
        assert result == 5

    def test_accumulates(self, conn):
        increment_daily_proactive_count(conn, increment=3)
        result = increment_daily_proactive_count(conn, increment=2)
        assert result == 5


# ---------------------------------------------------------------------------
# has_daily_budget
# ---------------------------------------------------------------------------


class TestHasDailyBudget:
    def test_true_when_under_budget(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_DAILY_BUDGET", "10")
        assert has_daily_budget(conn) is True

    def test_false_at_budget_limit(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_DAILY_BUDGET", "3")
        increment_daily_proactive_count(conn, increment=3)
        assert has_daily_budget(conn) is False

    def test_false_over_budget(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_DAILY_BUDGET", "2")
        increment_daily_proactive_count(conn, increment=5)
        assert has_daily_budget(conn) is False

    def test_uses_default_when_env_missing(self, conn, monkeypatch):
        monkeypatch.delenv("UA_PROACTIVE_DAILY_BUDGET", raising=False)
        # Default is 10, so budget exists at 0
        assert has_daily_budget(conn) is True


# ---------------------------------------------------------------------------
# get_budget_remaining
# ---------------------------------------------------------------------------


class TestGetBudgetRemaining:
    def test_full_budget_at_start(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_DAILY_BUDGET", "10")
        assert get_budget_remaining(conn) == 10

    def test_decreases_with_usage(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_DAILY_BUDGET", "10")
        increment_daily_proactive_count(conn, increment=3)
        assert get_budget_remaining(conn) == 7

    def test_clamps_to_zero(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_DAILY_BUDGET", "2")
        increment_daily_proactive_count(conn, increment=5)
        assert get_budget_remaining(conn) == 0

    def test_uses_default_budget(self, conn, monkeypatch):
        monkeypatch.delenv("UA_PROACTIVE_DAILY_BUDGET", raising=False)
        assert get_budget_remaining(conn) == DEFAULT_DAILY_BUDGET


# ---------------------------------------------------------------------------
# T14 — split keys: the real budget counter only advances on a true
# task_hub.upsert_item INSERT, never on a prompt-injection attempt or a
# re-upsert of an existing task_id.
# ---------------------------------------------------------------------------


class TestBudgetAdvancesOnlyOnRealInsert:
    """The counter that gates has_daily_budget/get_budget_remaining must
    track real task creations (task_hub.upsert_item's INSERT path), not
    reflection prompt-injection attempts. Regression coverage for the bug
    where heartbeat_service.py incremented this counter every time it built
    a reflection prompt, whether or not the agent actually created a task.
    """

    def test_new_reflection_task_increments_budget(self, conn):
        from universal_agent import task_hub

        assert get_daily_proactive_count(conn) == 0
        task_hub.upsert_item(conn, {
            "task_id": "t14-reflection-1",
            "source_kind": "reflection",
            "title": "New reflection proposal",
            "description": "desc",
            "project_key": "immediate",
        })
        assert get_daily_proactive_count(conn) == 1

    def test_new_proactive_signal_task_increments_budget(self, conn):
        from universal_agent import task_hub

        task_hub.upsert_item(conn, {
            "task_id": "t14-signal-1",
            "source_kind": "proactive_signal",
            "title": "New signal task",
            "description": "desc",
            "project_key": "immediate",
        })
        assert get_daily_proactive_count(conn) == 1

    def test_re_upsert_of_existing_task_does_not_double_count(self, conn):
        from universal_agent import task_hub

        item = {
            "task_id": "t14-reflection-reupsert",
            "source_kind": "reflection",
            "title": "Reflection proposal",
            "description": "desc",
            "project_key": "immediate",
        }
        task_hub.upsert_item(conn, item)
        assert get_daily_proactive_count(conn) == 1
        # Re-upsert the SAME task_id (e.g. a status transition) -- this must
        # NOT advance the counter again; it already counted once at creation.
        task_hub.upsert_item(conn, {**item, "status": task_hub.TASK_STATUS_IN_PROGRESS})
        assert get_daily_proactive_count(conn) == 1
        task_hub.upsert_item(conn, {**item, "status": task_hub.TASK_STATUS_COMPLETED})
        assert get_daily_proactive_count(conn) == 1

    def test_other_source_kinds_never_count(self, conn):
        from universal_agent import task_hub

        for kind in ("system_command", "internal", "proactive_codie", "csi"):
            task_hub.upsert_item(conn, {
                "task_id": f"t14-other-{kind}",
                "source_kind": kind,
                "title": f"{kind} task",
                "description": "desc",
                "project_key": "immediate",
            })
        assert get_daily_proactive_count(conn) == 0

    def test_multiple_new_reflection_tasks_accumulate(self, conn):
        from universal_agent import task_hub

        for i in range(3):
            task_hub.upsert_item(conn, {
                "task_id": f"t14-multi-{i}",
                "source_kind": "reflection",
                "title": f"Proposal {i}",
                "description": "desc",
                "project_key": "immediate",
            })
        assert get_daily_proactive_count(conn) == 3


# ---------------------------------------------------------------------------
# T14 — ideation ticks: a SEPARATE counter from the real budget, for pacing/
# observability only. Must never move the real budget counter and vice versa.
# ---------------------------------------------------------------------------


class TestIdeationTicks:
    def test_starts_at_zero(self, conn):
        assert get_ideation_ticks(conn) == 0

    def test_increments_independently_of_task_budget(self, conn):
        increment_ideation_ticks(conn)
        increment_ideation_ticks(conn, increment=2)
        assert get_ideation_ticks(conn) == 3
        # The real task budget must be completely untouched by tick increments.
        assert get_daily_proactive_count(conn) == 0

    def test_incrementing_task_budget_does_not_move_ticks(self, conn):
        increment_daily_proactive_count(conn, increment=4)
        assert get_daily_proactive_count(conn) == 4
        assert get_ideation_ticks(conn) == 0

    @pytest.mark.parametrize("ceiling", [5])
    def test_has_ideation_tick_budget_true_under_ceiling(self, conn, monkeypatch, ceiling):
        monkeypatch.setenv("UA_PROACTIVE_IDEATION_TICK_BUDGET", str(ceiling))
        assert has_ideation_tick_budget(conn) is True

    @pytest.mark.parametrize("ceiling", [5])
    def test_has_ideation_tick_budget_false_at_ceiling(self, conn, monkeypatch, ceiling):
        monkeypatch.setenv("UA_PROACTIVE_IDEATION_TICK_BUDGET", str(ceiling))
        for _ in range(ceiling):
            increment_ideation_ticks(conn)
        assert has_ideation_tick_budget(conn) is False

    def test_uses_default_ceiling_when_env_missing(self, conn, monkeypatch):
        from universal_agent.services.proactive_budget import (
            DEFAULT_IDEATION_TICK_BUDGET,
        )

        monkeypatch.delenv("UA_PROACTIVE_IDEATION_TICK_BUDGET", raising=False)
        for _ in range(DEFAULT_IDEATION_TICK_BUDGET):
            increment_ideation_ticks(conn)
        assert has_ideation_tick_budget(conn) is False


# ---------------------------------------------------------------------------
# T14 — should_ideate_now respects the Houston 06:00-22:00 active window
# (services.dormancy), independent of the interval-pacing gate.
# ---------------------------------------------------------------------------


class TestShouldIdeateNowActiveWindow:
    def test_refuses_outside_active_window_even_with_pacing_disabled(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS", "0")  # pacing off
        night = _houston_epoch(2026, 1, 15, 3)  # 3 AM Houston -- dormant
        assert should_ideate_now(conn, now=night) is False

    def test_allows_inside_active_window_with_pacing_disabled(self, conn, monkeypatch):
        monkeypatch.setenv("UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS", "0")
        day = _houston_epoch(2026, 1, 15, 14)  # 2 PM Houston -- active
        assert should_ideate_now(conn, now=day) is True

    def test_refuses_just_before_active_window_starts(self, conn):
        edge = _houston_epoch(2026, 1, 15, 5, 59)  # 05:59 -- still dormant
        assert should_ideate_now(conn, now=edge) is False

    def test_allows_at_active_window_start(self, conn):
        edge = _houston_epoch(2026, 1, 15, 6, 0)  # 06:00 -- inclusive start
        assert should_ideate_now(conn, now=edge) is True

    def test_refuses_at_active_window_end(self, conn):
        edge = _houston_epoch(2026, 1, 15, 22, 0)  # 22:00 -- exclusive end, dormant
        assert should_ideate_now(conn, now=edge) is False
