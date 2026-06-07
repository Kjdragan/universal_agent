"""Tests for the cron Kanban board helpers in gateway_server.

Covers the two pure helpers that drive the operator-visible behavior:
- ``_is_idle_cron_board_row``: idle cron linkage rows are hidden from the board,
  while running/failed cron rows and all non-cron rows stay visible.
- ``_merge_completed_dashboard_cards``: per-run cron cards interleave with agent
  completions newest-first and the result is capped at the limit.
"""
from __future__ import annotations

from universal_agent import gateway_server as gs


def test_idle_cron_row_is_hidden() -> None:
    # The resting perpetual cron:<job> row (status='open' -> board_lane
    # 'not_assigned') is noise and must be hidden.
    assert gs._is_idle_cron_board_row(
        {"source_kind": "cron_run", "board_lane": "not_assigned"}
    ) is True


def test_running_and_failed_cron_rows_stay_visible() -> None:
    # A cron earns a board card while running or when it has failed and needs eyes.
    assert gs._is_idle_cron_board_row(
        {"source_kind": "cron_run", "board_lane": "in_progress"}
    ) is False
    assert gs._is_idle_cron_board_row(
        {"source_kind": "cron_run", "board_lane": "needs_review"}
    ) is False
    assert gs._is_idle_cron_board_row(
        {"source_kind": "cron_run", "board_lane": "blocked"}
    ) is False


def test_non_cron_rows_are_never_hidden() -> None:
    # Real agent work in Not Assigned must never be filtered by this predicate.
    assert gs._is_idle_cron_board_row(
        {"source_kind": "vp_mission", "board_lane": "not_assigned"}
    ) is False
    assert gs._is_idle_cron_board_row(
        {"source_kind": "convergence_candidate", "board_lane": "not_assigned"}
    ) is False
    assert gs._is_idle_cron_board_row({}) is False


def test_merge_completed_cards_newest_first() -> None:
    task_cards = [
        {"task_id": "t1", "completed_at": "2026-06-06T05:00:00+00:00"},
        {"task_id": "t2", "completed_at": "2026-06-06T09:00:00+00:00"},
    ]
    cron_cards = [
        {"task_id": "cron:hourly_intel_digest", "run_id": "r1", "completed_at": "2026-06-06T07:00:00+00:00"},
    ]
    merged = gs._merge_completed_dashboard_cards(task_cards, cron_cards, limit=10)
    assert [c.get("run_id") or c["task_id"] for c in merged] == ["t2", "r1", "t1"]


def test_merge_completed_cards_respects_limit() -> None:
    task_cards = [{"task_id": f"t{i}", "completed_at": f"2026-06-06T0{i}:00:00+00:00"} for i in range(1, 5)]
    cron_cards = [{"task_id": "cron:x", "run_id": f"r{i}", "completed_at": f"2026-06-06T1{i}:00:00+00:00"} for i in range(1, 5)]
    merged = gs._merge_completed_dashboard_cards(task_cards, cron_cards, limit=3)
    assert len(merged) == 3
    # Newest three are the cron runs at 14/13/12 (r4, r3, r2).
    assert [c["run_id"] for c in merged] == ["r4", "r3", "r2"]


def test_merge_handles_empty_cron_cards() -> None:
    task_cards = [{"task_id": "t1", "completed_at": "2026-06-06T05:00:00+00:00"}]
    assert gs._merge_completed_dashboard_cards(task_cards, [], limit=10) == task_cards
    assert gs._merge_completed_dashboard_cards(task_cards, None, limit=10) == task_cards
