"""Unit tests for the async dispatch driver dispatch_claimed (M2 / D3).

Uses an in-memory task_hub DB (seed via upsert_item) and an injected stub
dispatch_fn (mirroring test_atlas_direct_dispatch's _stub_dispatch shape).
"""

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import priority_dispatcher as pd
from universal_agent.services.agent_router import AGENT_CODER, AGENT_GENERAL
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
    # Keep prefer-ATLAS off unless a test opts in; reset the global governor so
    # one test's backoff/api_down state can't leak into the next.
    monkeypatch.delenv("UA_DISPATCHER_PREFER_ATLAS", raising=False)
    CapacityGovernor.reset_instance()
    yield
    CapacityGovernor.reset_instance()


def _seed(conn, *, task_id, source_kind="internal", metadata=None, description="do the thing"):
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "title": task_id,
            "description": description,
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "metadata": metadata or {},
        },
    )
    return task_hub.get_item(conn, task_id)


def _make_stub():
    calls = []

    async def _stub(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "mission_id": "vp-mission-stub"}

    return _stub, calls


@pytest.mark.asyncio
async def test_vp_decision_with_free_slot_dispatches_and_delegates(conn):
    item = _seed(conn, task_id="t1", source_kind="tutorial_build")  # coding -> CODER
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["vp_id"] == AGENT_CODER
    assert call["task_id"] == "t1"
    assert call["idempotency_key"] == "dispatch-t1"
    assert call["mission_type"] == "task"
    assert call["source_session_id"] == "priority_dispatcher"

    d = decisions[0]
    assert d.dispatched is True
    assert d.deferred is False
    assert d.mission_id == "vp-mission-stub"

    # The delegated lifecycle mutation must have fired.
    row = task_hub.get_item(conn, "t1")
    assert row["status"] == task_hub.TASK_STATUS_DELEGATED
    delegation = (row.get("metadata") or {}).get("delegation") or {}
    assert delegation.get("delegate_target") == AGENT_CODER
    assert delegation.get("mission_id") == "vp-mission-stub"


@pytest.mark.asyncio
async def test_all_coder_slots_full_defers_without_dispatch(conn):
    item = _seed(conn, task_id="t2", source_kind="tutorial_build")  # coding -> CODER
    stub, calls = _make_stub()
    # One coder already active; default cap UA_MAX_CONCURRENT_VP_CODER=1.
    active = [{"agent_id": AGENT_CODER}]

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=active, dispatch_fn=stub
    )

    assert calls == []  # never dispatched
    d = decisions[0]
    assert d.deferred is True
    assert d.dispatched is False
    # Task stays open (not delegated) and is annotated for Simone's prompt.
    row = task_hub.get_item(conn, "t2")
    assert row["status"] == task_hub.TASK_STATUS_OPEN
    assert item["_routing"]["agent_id"] == AGENT_CODER
    assert item["_routing"]["should_delegate"] is True


@pytest.mark.asyncio
async def test_governor_shed_defers(conn, monkeypatch):
    item = _seed(conn, task_id="t3", source_kind="tutorial_build")
    stub, calls = _make_stub()
    monkeypatch.setattr(
        CapacityGovernor, "can_dispatch", lambda self: (False, "test_backoff")
    )

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )

    assert calls == []
    assert decisions[0].deferred is True
    assert decisions[0].dispatched is False


@pytest.mark.asyncio
async def test_per_agent_cap_independence(conn, monkeypatch):
    monkeypatch.setenv("UA_DISPATCHER_PREFER_ATLAS", "1")  # allow general -> ATLAS
    coder_item = _seed(
        conn, task_id="c1", metadata={"preferred_vp": AGENT_CODER}
    )
    general_item = _seed(
        conn, task_id="g1", metadata={"preferred_vp": AGENT_GENERAL}
    )
    stub, calls = _make_stub()
    # Coder full (cap 1), general free (cap 2).
    active = [{"agent_id": AGENT_CODER}]

    decisions = await pd.dispatch_claimed(
        conn, [coder_item, general_item], active_assignments=active, dispatch_fn=stub
    )

    # Only the general task dispatched; the coder task deferred.
    assert len(calls) == 1
    assert calls[0]["vp_id"] == AGENT_GENERAL
    assert calls[0]["task_id"] == "g1"
    assert calls[0]["mission_type"] == "proactive_general"

    by_id = {d.task_id: d for d in decisions}
    assert by_id["g1"].dispatched is True
    assert by_id["c1"].deferred is True
    assert by_id["c1"].dispatched is False
    assert task_hub.get_item(conn, "g1")["status"] == task_hub.TASK_STATUS_DELEGATED
    assert task_hub.get_item(conn, "c1")["status"] == task_hub.TASK_STATUS_OPEN


@pytest.mark.asyncio
async def test_dispatch_failure_falls_through_to_simone(conn):
    item = _seed(conn, task_id="t4", source_kind="tutorial_build")

    async def _boom(**kwargs):
        raise RuntimeError("dispatch exploded")

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=_boom
    )

    d = decisions[0]
    assert d.dispatched is False
    assert d.deferred is True
    # Task not delegated; left in the residue with a routing hint for Simone.
    assert task_hub.get_item(conn, "t4")["status"] == task_hub.TASK_STATUS_OPEN
    assert item["_routing"]["should_delegate"] is True


@pytest.mark.asyncio
async def test_simone_bound_task_is_not_dispatched(conn):
    item = _seed(conn, task_id="t5", source_kind="chat_panel")  # chat -> Simone
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )

    assert calls == []
    d = decisions[0]
    assert d.is_vp is False
    assert d.dispatched is False
    assert d.deferred is False
    assert item["_routing"]["agent_id"] == "simone"
    assert item["_routing"]["should_delegate"] is False


@pytest.mark.asyncio
async def test_ambiguous_tail_consults_classifier(conn, monkeypatch):
    item = _seed(conn, task_id="t6", source_kind="internal", description="fix the parser bug")

    async def _fake_route(**kwargs):
        return {
            "agent_id": AGENT_CODER,
            "method": "llm",
            "confidence": "high",
            "should_delegate": True,
        }

    from universal_agent.services import llm_classifier

    monkeypatch.setattr(llm_classifier, "classify_agent_route", _fake_route)
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )

    d = decisions[0]
    assert d.agent_id == AGENT_CODER
    assert d.method == "classifier:llm"
    assert d.dispatched is True
    assert len(calls) == 1
    assert calls[0]["vp_id"] == AGENT_CODER
    assert task_hub.get_item(conn, "t6")["status"] == task_hub.TASK_STATUS_DELEGATED


@pytest.mark.asyncio
async def test_idempotency_key_includes_run_id_for_retry_safety(conn):
    # A per-attempt key (carrying the fresh-per-claim run_id) is what lets a
    # reopened+reclaimed task get a NEW mission instead of colliding with the
    # prior (terminal/running) mission id.
    item = _seed(conn, task_id="t7", source_kind="tutorial_build")
    item["workflow_run_id"] = "run_xyz"
    seen = []

    async def _stub(**kwargs):
        seen.append(kwargs["idempotency_key"])
        return {"ok": True, "mission_id": "m1"}

    await pd.dispatch_claimed(conn, [item], active_assignments=[], dispatch_fn=_stub)
    assert seen == ["dispatch-t7-run_xyz"]


@pytest.mark.asyncio
async def test_no_duplicate_delegate_when_impl_self_delegated(conn, monkeypatch):
    # Simulate dispatch_vp_mission self-delegating the task (as it does for
    # cody_demo_task): the dispatcher must detect the existing delegation to the
    # same mission and NOT write a second delegate/evaluation row.
    item = _seed(conn, task_id="t8", source_kind="cody_demo_task")

    async def _self_delegating_stub(**kwargs):
        task_hub.perform_task_action(
            conn,
            task_id=kwargs["task_id"],
            action="delegate",
            reason=kwargs["vp_id"],
            note="mission_id=impl-mission",
            agent_id="simone_vp_dispatch",
        )
        return {"ok": True, "mission_id": "impl-mission"}

    calls = {"perform": 0}
    real_perform = task_hub.perform_task_action

    def _counting_perform(*a, **k):
        if k.get("agent_id") == "priority_dispatcher":
            calls["perform"] += 1
        return real_perform(*a, **k)

    monkeypatch.setattr(task_hub, "perform_task_action", _counting_perform)

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=_self_delegating_stub
    )

    assert decisions[0].dispatched is True
    assert decisions[0].mission_id == "impl-mission"
    # The dispatcher did NOT issue its own delegate (the impl already did).
    assert calls["perform"] == 0
    row = task_hub.get_item(conn, "t8")
    assert row["status"] == task_hub.TASK_STATUS_DELEGATED


@pytest.mark.asyncio
async def test_empty_claimed_is_noop(conn):
    stub, calls = _make_stub()
    decisions = await pd.dispatch_claimed(conn, [], active_assignments=[], dispatch_fn=stub)
    assert decisions == []
    assert calls == []
