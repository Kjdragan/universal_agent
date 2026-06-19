"""Unit tests for the Homer (vp.general.secondary) general-pool routing.

Homer is an opportunistic SECOND general VP that fills the second concurrency
slot ONLY when CODIE is idle, so peak concurrent VP missions stays <= 2
(ATLAS + exactly one of {CODIE, HOMER}).

The concurrency gate is sourced from the AUTHORITATIVE ``vp_missions`` live
state (``_live_vp_active_counts``), NOT the ``active_assignments`` snapshot —
because a task dispatched to a VP is marked ``delegated`` and its task-hub
assignment is completed, so a *running* VP mission is invisible to
``get_agent_activity`` across sweeps.

Hard requirements exercised here:
  R1 Parameterized presence — HOMER absent from ``UA_VP_ENABLED_IDS`` => the
     dispatcher behaves EXACTLY as today (atlas-only), never routes to HOMER,
     and never even reads the live vp_missions state.
  R2 Peak <= 2 — HOMER spills only when ATLAS full AND CODIE idle; and CODIE
     defers when a HOMER mission is live (mutual exclusion).
  R3 Pins — an explicit operator ``target_agent`` pin never reaches HOMER; the
     producer pre-tag and the classifier tail are poolable (HOMER-eligible).
"""

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import priority_dispatcher as pd
from universal_agent.services.agent_router import (
    AGENT_CODER,
    AGENT_GENERAL,
    AGENT_GENERAL_SECONDARY,
)
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
    # Prefer-ATLAS must be ON for general tasks to route to the VP pool at all.
    monkeypatch.setenv("UA_DISPATCHER_PREFER_ATLAS", "1")
    monkeypatch.delenv("UA_VP_ENABLED_IDS", raising=False)
    CapacityGovernor.reset_instance()
    yield
    CapacityGovernor.reset_instance()


def _enable_homer(monkeypatch):
    monkeypatch.setenv(
        "UA_VP_ENABLED_IDS",
        "vp.coder.primary,vp.general.primary,vp.general.secondary",
    )


def _set_live_counts(monkeypatch, *, atlas, homer, coder):
    """Inject the authoritative live (atlas, homer, coder) mission counts."""
    monkeypatch.setattr(pd, "_live_vp_active_counts", lambda: (atlas, homer, coder))


def _seed(conn, *, task_id, metadata=None, source_kind="convergence_detection"):
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "title": task_id,
            "description": "general research work",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "metadata": metadata or {"preferred_vp": AGENT_GENERAL},
        },
    )
    return task_hub.get_item(conn, task_id)


def _make_stub():
    calls = []

    async def _stub(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "mission_id": f"mission-{kwargs['task_id']}"}

    return _stub, calls


# ---------------------------------------------------------------------------
# Pure: _pick_general_target
# ---------------------------------------------------------------------------


def test_pick_general_target_not_eligible_is_legacy():
    # homer_eligible=False (HOMER disabled OR an explicit ATLAS pin): all general
    # goes to ATLAS up to the POOL cap (atlas+homer), never to HOMER.
    assert (
        pd._pick_general_target(coder_used=0, atlas_used=0, homer_used=0, max_general=2, homer_eligible=False)
        == AGENT_GENERAL
    )
    assert (
        pd._pick_general_target(coder_used=0, atlas_used=1, homer_used=0, max_general=2, homer_eligible=False)
        == AGENT_GENERAL
    )
    assert (
        pd._pick_general_target(coder_used=0, atlas_used=2, homer_used=0, max_general=2, homer_eligible=False)
        is None
    )


def test_pick_general_target_pin_is_pool_aware_with_live_homer():
    # A pin (not eligible) must count HOMER toward the pool cap, so it can't queue
    # a 2nd ATLAS beside a running HOMER (which would be a 3rd general mission).
    assert (
        pd._pick_general_target(coder_used=0, atlas_used=1, homer_used=1, max_general=2, homer_eligible=False)
        is None
    )


def test_pick_general_target_eligible_spills_only_when_cody_idle():
    # ATLAS free -> ATLAS first
    assert (
        pd._pick_general_target(coder_used=0, atlas_used=0, homer_used=0, max_general=2, homer_eligible=True)
        == AGENT_GENERAL
    )
    # ATLAS full, Cody idle, HOMER free -> HOMER
    assert (
        pd._pick_general_target(coder_used=0, atlas_used=1, homer_used=0, max_general=2, homer_eligible=True)
        == AGENT_GENERAL_SECONDARY
    )
    # ATLAS full, Cody ACTIVE -> defer (gate)
    assert (
        pd._pick_general_target(coder_used=1, atlas_used=1, homer_used=0, max_general=2, homer_eligible=True)
        is None
    )
    # ATLAS full, Cody idle, HOMER also full -> defer
    assert (
        pd._pick_general_target(coder_used=0, atlas_used=1, homer_used=1, max_general=2, homer_eligible=True)
        is None
    )


# ---------------------------------------------------------------------------
# Live-state reader: _live_vp_active_counts (real temp vp_missions db)
# ---------------------------------------------------------------------------


def test_live_vp_active_counts_counts_only_running(tmp_path, monkeypatch):
    # RUNNING only — a queued mission must NOT count (else a stuck-queued HOMER
    # mission would wedge the CODIE lane forever).
    from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
    from universal_agent.durable.migrations import ensure_schema
    from universal_agent.durable.state import upsert_vp_mission, upsert_vp_session

    monkeypatch.setenv("UA_VP_DB_PATH", str(tmp_path / "vp.db"))
    with connect_runtime_db(get_vp_db_path()) as c:
        ensure_schema(c)
        # vp_missions has a FK to vp_sessions — create the session rows first.
        for vid in (AGENT_GENERAL, AGENT_GENERAL_SECONDARY, AGENT_CODER):
            upsert_vp_session(c, vp_id=vid, runtime_id="rt.test", status="idle", session_id=f"{vid}.test")
        upsert_vp_mission(c, "m1", AGENT_GENERAL, "running", "atlas job")
        upsert_vp_mission(c, "m2", AGENT_GENERAL, "queued", "atlas job 2")  # queued -> excluded
        upsert_vp_mission(c, "m3", AGENT_GENERAL_SECONDARY, "running", "homer job")
        upsert_vp_mission(c, "m4", AGENT_GENERAL_SECONDARY, "queued", "homer job 2")  # queued -> excluded
        upsert_vp_mission(c, "m5", AGENT_CODER, "running", "cody job")
        upsert_vp_mission(c, "m6", AGENT_CODER, "completed", "old cody job")  # terminal -> excluded

    atlas, homer, coder = pd._live_vp_active_counts()
    assert (atlas, homer, coder) == (1, 1, 1)


def test_live_vp_active_counts_fails_open(monkeypatch):
    # On any read error, return zeros (fail-safe: HOMER won't spill, CODIE not deferred).
    monkeypatch.setenv("UA_VP_DB_PATH", "/nonexistent/dir/vp.db")
    assert pd._live_vp_active_counts() == (0, 0, 0)


# ---------------------------------------------------------------------------
# dispatch_claimed integration (live counts injected)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_atlas_busy_cody_idle_spills_single_general_to_homer(conn, monkeypatch):
    # THE key fix: with batch=1, a RUNNING ATLAS mission (live) makes the one
    # general task spill to HOMER — impossible under the old snapshot gate.
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=1, homer=0, coder=0)
    g1 = _seed(conn, task_id="g1")
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(conn, [g1], active_assignments=[], dispatch_fn=stub)

    assert [c["vp_id"] for c in calls] == [AGENT_GENERAL_SECONDARY]
    assert decisions[0].dispatched is True


@pytest.mark.asyncio
async def test_atlas_free_routes_to_atlas(conn, monkeypatch):
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=0, homer=0, coder=0)
    g1 = _seed(conn, task_id="g1")
    stub, calls = _make_stub()

    await pd.dispatch_claimed(conn, [g1], active_assignments=[], dispatch_fn=stub)
    assert [c["vp_id"] for c in calls] == [AGENT_GENERAL]


@pytest.mark.asyncio
async def test_homer_gated_when_cody_active(conn, monkeypatch):
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=1, homer=0, coder=1)  # ATLAS full, Cody running
    g1 = _seed(conn, task_id="g1")
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(conn, [g1], active_assignments=[], dispatch_fn=stub)
    assert calls == []
    assert decisions[0].deferred is True
    assert task_hub.get_item(conn, "g1")["status"] == task_hub.TASK_STATUS_OPEN


@pytest.mark.asyncio
async def test_coder_defers_when_homer_live(conn, monkeypatch):
    # Bidirectional mutual exclusion: a live HOMER mission blocks a new CODIE
    # dispatch so peak stays <= 2 (no ATLAS + HOMER + CODIE = 3).
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=1, homer=1, coder=0)
    c1 = _seed(conn, task_id="c1", source_kind="tutorial_build")  # coding -> CODER
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(conn, [c1], active_assignments=[], dispatch_fn=stub)
    assert calls == []
    assert decisions[0].deferred is True


@pytest.mark.asyncio
async def test_coder_dispatches_when_homer_idle(conn, monkeypatch):
    # Sanity: HOMER not live -> CODIE dispatches normally (cap 1, slot free).
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=1, homer=0, coder=0)
    c1 = _seed(conn, task_id="c1", source_kind="tutorial_build")
    stub, calls = _make_stub()

    await pd.dispatch_claimed(conn, [c1], active_assignments=[], dispatch_fn=stub)
    assert [c["vp_id"] for c in calls] == [AGENT_CODER]


@pytest.mark.asyncio
async def test_explicit_atlas_pin_never_spills_to_homer(conn, monkeypatch):
    # Same live state (ATLAS busy, Cody idle): an explicit target_agent pin stays
    # ATLAS, while a producer pre-tag spills to HOMER.
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=1, homer=0, coder=0)
    pinned = _seed(conn, task_id="pin1", metadata={"target_agent": AGENT_GENERAL})
    pooled = _seed(conn, task_id="pool1", metadata={"preferred_vp": AGENT_GENERAL})
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [pinned, pooled], active_assignments=[], dispatch_fn=stub
    )
    by_task = {c["task_id"]: c["vp_id"] for c in calls}
    # The pin stays ATLAS (queues a 2nd ATLAS mission); the pool task spills to HOMER.
    assert by_task.get("pin1") == AGENT_GENERAL
    assert by_task.get("pool1") == AGENT_GENERAL_SECONDARY
    assert AGENT_GENERAL_SECONDARY not in [
        d.agent_id for d in decisions if d.task_id == "pin1"
    ]


@pytest.mark.asyncio
async def test_pin_defers_when_pool_full_with_live_homer(conn, monkeypatch):
    # Pool full as ATLAS(1 running) + HOMER(1 running): an explicit ATLAS pin must
    # DEFER (not queue a 2nd ATLAS beside the running HOMER = 3 general missions).
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=1, homer=1, coder=0)
    pinned = _seed(conn, task_id="pin1", metadata={"target_agent": AGENT_GENERAL})
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(conn, [pinned], active_assignments=[], dispatch_fn=stub)
    assert calls == []
    assert decisions[0].deferred is True


@pytest.mark.asyncio
async def test_queued_homer_does_not_wedge_coder(conn, monkeypatch):
    # A HOMER mission that is QUEUED-but-not-running reports homer running==0
    # (see _live_vp_active_counts), so CODIE is NOT deferred — no lane deadlock.
    _enable_homer(monkeypatch)
    _set_live_counts(monkeypatch, atlas=1, homer=0, coder=0)  # homer queued -> 0 running
    c1 = _seed(conn, task_id="c1", source_kind="tutorial_build")
    stub, calls = _make_stub()

    await pd.dispatch_claimed(conn, [c1], active_assignments=[], dispatch_fn=stub)
    assert [c["vp_id"] for c in calls] == [AGENT_CODER]


@pytest.mark.asyncio
async def test_homer_disabled_never_routes_to_secondary_and_skips_live_read(conn, monkeypatch):
    # R1: HOMER absent -> legacy active_assignments path; never reads live state.
    monkeypatch.delenv("UA_VP_ENABLED_IDS", raising=False)

    def _boom():
        raise AssertionError("_live_vp_active_counts must NOT be called when HOMER disabled")

    monkeypatch.setattr(pd, "_live_vp_active_counts", _boom)
    g1 = _seed(conn, task_id="g1")
    g2 = _seed(conn, task_id="g2")
    stub, calls = _make_stub()

    await pd.dispatch_claimed(conn, [g1, g2], active_assignments=[], dispatch_fn=stub)
    targets = sorted(c["vp_id"] for c in calls)
    assert targets == [AGENT_GENERAL, AGENT_GENERAL]
    assert AGENT_GENERAL_SECONDARY not in targets
