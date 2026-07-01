"""Component C — proactive tutorial_build daily BUILD cap (OUTFLOW control).

The bespoke proactive demo lane had no real per-day BUILD cap: only the
auto-route INFLOW ceiling (``UA_DEMO_BUILD_DAILY_CEILING``) throttled queueing,
and it was bypassed. The cap is enforced at the single dispatch point
(``priority_dispatcher.dispatch_claimed``): once ``UA_PROACTIVE_DEMO_DAILY_CAP``
(default 3) tutorial_build builds have been DISPATCHED today (America/Chicago —
the shared ``utils.day_boundary.chicago_day_start_iso`` boundary), further ones
are deferred (left queued, never cancelled).

"Dispatched today" is counted from ``metadata.delegation.delegated_at`` — the
durable per-task marker the delegate verb writes — which persists through the
task's later lifecycle.
"""

from datetime import datetime, timezone
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import priority_dispatcher as pd
from universal_agent.services.agent_router import AGENT_CODER
from universal_agent.services.capacity_governor import CapacityGovernor


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _reset_governor_and_flags(monkeypatch):
    monkeypatch.delenv("UA_DISPATCHER_PREFER_ATLAS", raising=False)
    # Pin the cap explicitly so a polluted env can't shift the boundary.
    monkeypatch.setenv("UA_PROACTIVE_DEMO_DAILY_CAP", "3")
    CapacityGovernor.reset_instance()
    yield
    CapacityGovernor.reset_instance()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_open_tutorial_build(conn, task_id):
    """An agent_ready tutorial_build task waiting to be dispatched."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "tutorial_build",
            "title": task_id,
            "description": "build a private tutorial repo",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "metadata": {},
        },
    )
    return task_hub.get_item(conn, task_id)


def _seed_dispatched_today(conn, task_id, *, delegated_at=None):
    """A tutorial_build already dispatched today (delegation marker set)."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "tutorial_build",
            "title": task_id,
            "description": "already built today",
            "status": task_hub.TASK_STATUS_DELEGATED,
            "agent_ready": False,
            "metadata": {
                "delegation": {
                    "delegate_target": AGENT_CODER,
                    "delegated_at": delegated_at or _now_utc_iso(),
                    "mission_id": f"vp-mission-{task_id}",
                }
            },
        },
    )


def _make_stub():
    calls = []

    async def _stub(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "mission_id": f"vp-mission-{kwargs.get('task_id')}"}

    return _stub, calls


def test_count_helper_only_counts_today(conn):
    _seed_dispatched_today(conn, "tb-today-1")
    _seed_dispatched_today(conn, "tb-today-2")
    # Yesterday (lexicographically < today's America/Chicago midnight) must not count.
    _seed_dispatched_today(
        conn, "tb-yesterday", delegated_at="2000-01-01T00:00:00+00:00"
    )
    assert pd._count_dispatched_tutorial_builds_today(conn) == 2


@pytest.mark.asyncio
async def test_fourth_build_is_capped_and_left_queued(conn):
    # 3 builds already dispatched today → the 4th must be deferred.
    for i in range(3):
        _seed_dispatched_today(conn, f"tb-prior-{i}")
    item = _seed_open_tutorial_build(conn, "tb-fourth")
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )

    assert calls == []  # never dispatched
    d = decisions[0]
    assert d.deferred is True
    assert d.dispatched is False
    # Left queued (still open, not cancelled) with a routing hint for Simone.
    row = task_hub.get_item(conn, "tb-fourth")
    assert row["status"] == task_hub.TASK_STATUS_OPEN
    assert item["_routing"]["agent_id"] == AGENT_CODER


@pytest.mark.asyncio
async def test_third_build_under_cap_dispatches(conn):
    # Only 2 dispatched today → the next (the 3rd) is allowed.
    for i in range(2):
        _seed_dispatched_today(conn, f"tb-prior-{i}")
    item = _seed_open_tutorial_build(conn, "tb-third")
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )

    assert len(calls) == 1
    assert calls[0]["vp_id"] == AGENT_CODER
    assert calls[0]["task_id"] == "tb-third"
    d = decisions[0]
    assert d.dispatched is True
    assert d.deferred is False
    assert task_hub.get_item(conn, "tb-third")["status"] == task_hub.TASK_STATUS_DELEGATED


@pytest.mark.asyncio
async def test_first_three_allowed_fourth_blocked_across_sweeps(conn):
    # End-to-end: four independent sweeps, each a fresh tutorial_build. The
    # real delegate marker accrues in the DB, so sweeps 1-3 dispatch and the
    # 4th is capped — without any pre-seeding.
    dispatched, deferred = [], []
    for i in range(4):
        item = _seed_open_tutorial_build(conn, f"tb-seq-{i}")
        stub, calls = _make_stub()
        decisions = await pd.dispatch_claimed(
            conn, [item], active_assignments=[], dispatch_fn=stub
        )
        (dispatched if decisions[0].dispatched else deferred).append(f"tb-seq-{i}")

    assert dispatched == ["tb-seq-0", "tb-seq-1", "tb-seq-2"]
    assert deferred == ["tb-seq-3"]
    assert task_hub.get_item(conn, "tb-seq-3")["status"] == task_hub.TASK_STATUS_OPEN


@pytest.mark.asyncio
async def test_cap_does_not_throttle_other_coder_lanes(conn):
    # A non-tutorial coder lane (cody_demo_task) is NOT subject to the demo cap.
    for i in range(5):
        _seed_dispatched_today(conn, f"tb-prior-{i}")  # cap already blown
    task_hub.upsert_item(
        conn,
        {
            "task_id": "cody-1",
            "source_kind": "cody_demo_task",
            "title": "cody-1",
            "description": "scaffold demo",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "metadata": {},
        },
    )
    item = task_hub.get_item(conn, "cody-1")
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )

    assert len(calls) == 1
    assert decisions[0].dispatched is True
