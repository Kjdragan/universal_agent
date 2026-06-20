"""Phase 1 — idle-burn kill + ideation pacing.

Covers two coupled changes:
- `proactive_budget.should_ideate_now` / `record_ideation_now` spread the daily
  ideation budget across the window instead of bursting at the reset.
- `_heartbeat_guard_policy` reaches the idle-skip branch regardless of
  `HEARTBEAT.md` content (the `has_heartbeat_content` term — which permanently
  wedged both the cheap-skip and ideation shut — was removed).
"""

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.heartbeat_service import _heartbeat_guard_policy
from universal_agent.services import proactive_budget


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    task_hub.ensure_schema(conn)
    return conn


def test_should_ideate_now_paces(monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("UA_PROACTIVE_IDEATION_JITTER_FRAC", "0.0")
    conn = _mem_conn()
    # First ideation of the window is always allowed.
    assert proactive_budget.should_ideate_now(conn, now=1000.0) is True
    proactive_budget.record_ideation_now(conn, now=1000.0)
    # Too soon -> paced out (cheap skip).
    assert proactive_budget.should_ideate_now(conn, now=1060.0) is False
    # Past the interval -> allowed again.
    assert proactive_budget.should_ideate_now(conn, now=1000.0 + 3601) is True


def test_should_ideate_now_disabled(monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS", "0")
    conn = _mem_conn()
    proactive_budget.record_ideation_now(conn, now=1000.0)
    assert proactive_budget.should_ideate_now(conn, now=1001.0) is True


def test_guard_reaches_idle_skip(monkeypatch):
    # Reflection disabled so an empty queue resolves to the cheap skip
    # ("no_actionable_work"). Before the fix, a non-empty HEARTBEAT.md would
    # have blocked this branch entirely.
    monkeypatch.setenv("UA_REFLECTION_ENABLED", "0")
    monkeypatch.setenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED", "1")
    policy = _heartbeat_guard_policy(
        actionable_count=0,
        brainstorm_candidate_count=0,
        system_event_count=0,
        has_exec_completion=False,
        pending_question_count=0,
        pending_demo_review_count=0,
    )
    assert policy["skip_reason"] == "no_actionable_work"


def test_guard_no_longer_accepts_has_heartbeat_content():
    # The removed parameter must be gone, not silently ignored.
    with pytest.raises(TypeError):
        _heartbeat_guard_policy(
            actionable_count=0,
            brainstorm_candidate_count=0,
            system_event_count=0,
            has_exec_completion=False,
            has_heartbeat_content=True,
        )


def test_guard_stays_awake_on_pending_question(monkeypatch):
    # A genuine operator ask (pending question) must still keep her awake.
    monkeypatch.setenv("UA_REFLECTION_ENABLED", "0")
    monkeypatch.setenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED", "1")
    policy = _heartbeat_guard_policy(
        actionable_count=0,
        brainstorm_candidate_count=0,
        system_event_count=0,
        has_exec_completion=False,
        pending_question_count=1,
        pending_demo_review_count=0,
    )
    assert policy["skip_reason"] is None
