"""Tests for the signal-curator minimum-interval dispatch floor.

Regression guard for the curation runaway documented in
docs/proactive_signals/insight_pipeline_remediation_plan_2026-05-28.md:

The ≥10-pending-cards "immediate" trigger fired on EVERY heartbeat whenever
cards sat unprocessed (which is exactly when curation missions are queued but
not yet run). That dispatched 20-30 curation missions/hour, burying the VP
mission queue and starving operator-facing work (including the convergence
evaluations that feed the hourly intel digest).

`should_run_curation` now enforces a wall-clock floor between dispatches,
regardless of card count.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import patch

import pytest

from universal_agent import task_hub
from universal_agent.services import signal_curator


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    yield c
    c.close()


def _set_last_run(conn, *, minutes_ago: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    task_hub._set_setting(conn, signal_curator._LAST_RUN_KEY, {"timestamp": ts})


def test_floor_blocks_immediate_trigger_when_recently_dispatched(conn):
    # 20 pending cards (well past the ≥10 immediate trigger), but the last
    # dispatch was 5 minutes ago — inside the 60-min floor. Must NOT re-run.
    _set_last_run(conn, minutes_ago=5)
    with patch.object(signal_curator, "_get_pending_card_count", return_value=20), \
         patch.object(signal_curator, "_proactive_queue_under_pressure", return_value=(False, "")):
        assert signal_curator.should_run_curation(conn) is False


def test_card_trigger_fires_once_floor_has_elapsed(conn):
    # Same 20 cards, but the last dispatch was 90 minutes ago — past the floor.
    _set_last_run(conn, minutes_ago=90)
    with patch.object(signal_curator, "_get_pending_card_count", return_value=20), \
         patch.object(signal_curator, "_proactive_queue_under_pressure", return_value=(False, "")):
        assert signal_curator.should_run_curation(conn) is True


def test_first_ever_run_with_enough_cards_still_fires(conn):
    # No last-run baseline at all: the card-count trigger should still work
    # (the floor only applies relative to a prior dispatch).
    with patch.object(signal_curator, "_get_pending_card_count", return_value=20), \
         patch.object(signal_curator, "_proactive_queue_under_pressure", return_value=(False, "")):
        assert signal_curator.should_run_curation(conn) is True


def test_zero_cards_never_runs(conn):
    with patch.object(signal_curator, "_get_pending_card_count", return_value=0):
        assert signal_curator.should_run_curation(conn) is False


def test_floor_is_configurable_to_zero(conn, monkeypatch):
    # Setting the floor to 0 restores the legacy every-cycle behaviour for
    # operators who want it.
    monkeypatch.setenv("UA_CURATOR_MIN_INTERVAL_MINUTES", "0")
    _set_last_run(conn, minutes_ago=1)
    with patch.object(signal_curator, "_get_pending_card_count", return_value=20), \
         patch.object(signal_curator, "_proactive_queue_under_pressure", return_value=(False, "")):
        assert signal_curator.should_run_curation(conn) is True
