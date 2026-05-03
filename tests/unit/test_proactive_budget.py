"""Tests for universal_agent.services.proactive_budget — daily budget tracking."""

import sqlite3

import pytest

from universal_agent.services.proactive_budget import (
    DEFAULT_DAILY_BUDGET,
    _parse_int_env,
    get_budget_remaining,
    get_daily_proactive_count,
    has_daily_budget,
    increment_daily_proactive_count,
)


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
