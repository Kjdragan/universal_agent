"""Unit tests for the operator-DIRECTED demo-build lane (S5).

Covers: the pure seed parser/slug/dedup, the intake function
(``queue_directed_demo_build`` — disabled-by-default, judge-skip, metadata
shape, idempotent dedup), the dispatcher wiring (coder-lane classification,
SEPARATE daily cap, directed-before-tutorial priority, cap/flag boundaries,
budget independence), and the source_kind enum memberships that make the worker
finalize + completion gate treat ``directed_build`` like ``tutorial_build``.
"""

import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import (
    directed_demo_builds as ddb,
    priority_dispatcher as pd,
)
from universal_agent.services.agent_router import AGENT_CODER
from universal_agent.services.capacity_governor import CapacityGovernor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def _clean_flags(monkeypatch):
    monkeypatch.delenv("UA_DISPATCHER_PREFER_ATLAS", raising=False)
    monkeypatch.delenv("UA_DIRECTED_DEMO_ENABLED", raising=False)
    monkeypatch.delenv("UA_DIRECTED_DEMO_DAILY_CAP", raising=False)
    # Proactive tutorial cap non-binding unless a test pins it (default is 0).
    monkeypatch.setenv("UA_PROACTIVE_DEMO_DAILY_CAP", "100")
    CapacityGovernor.reset_instance()
    yield
    CapacityGovernor.reset_instance()


def _make_stub():
    calls = []

    async def _stub(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "mission_id": "vp-mission-stub"}

    return _stub, calls


# ---------------------------------------------------------------------------
# Pure helpers: parser / slug / dedup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/demo build a thing", "build a thing"),
        ("/demo  the json-schema flag ", "the json-schema flag"),
        ("build a demo of the foo flag", "the foo flag"),
        ("Build me a demo of X", "X"),
        ("BUILD A DEMO OF: something cool", "something cool"),
    ],
)
def test_parse_directed_demo_seed_matches(text, expected):
    assert ddb.parse_directed_demo_seed(text) == expected


@pytest.mark.parametrize(
    "text",
    ["hello world", "/status", "/demo", "/demonstrate stuff", "just talking", ""],
)
def test_parse_directed_demo_seed_non_matches(text):
    assert ddb.parse_directed_demo_seed(text) is None


def test_dedup_task_id_normalizes_whitespace_and_case():
    a = ddb.directed_build_task_id("Build the JSON Schema flag")
    b = ddb.directed_build_task_id("build   the json schema   FLAG")
    assert a == b
    assert a.startswith("directed-build:")
    assert len(a.split(":", 1)[1]) == 16


def test_directed_demo_slug_is_bounded_and_clean():
    slug = ddb.directed_demo_slug("Claude Code --json-schema structured-output flag!!!")
    assert slug.startswith("claude-code-json-schema")
    assert len(slug) <= 40  # bounded
    assert not slug.endswith("-")  # no trailing dash after truncation
    assert ddb.directed_demo_slug("") == "directed"


# ---------------------------------------------------------------------------
# Intake: queue_directed_demo_build
# ---------------------------------------------------------------------------


def test_intake_disabled_by_default_creates_no_row(conn):
    # Master flag OFF (default): intake refuses, no Task Hub row.
    res = ddb.queue_directed_demo_build("Build a demo of X", conn=conn)
    assert res["status"] == "disabled"
    assert task_hub.get_item(conn, res["task_id"]) is None


def test_intake_empty_seed_is_invalid(conn, monkeypatch):
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    res = ddb.queue_directed_demo_build("   ", conn=conn)
    assert res["status"] == "invalid"


def test_intake_queues_directed_build_row_with_expected_shape(conn, monkeypatch):
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    seed = "Build a runnable demo of the Claude Code --json-schema flag"
    res = ddb.queue_directed_demo_build(
        seed, requested_by="tester", channel="gateway_api", conn=conn
    )
    assert res["status"] == "queued"

    row = task_hub.get_item(conn, res["task_id"])
    assert row is not None
    assert row["source_kind"] == "directed_build"
    meta = row.get("metadata") or {}
    assert meta["use_goal_loop"] is True  # /goal-driven, like tutorial_build
    assert meta["directed_seed"] == seed
    assert meta["directed_slug"] == res["slug"]
    assert meta["requested_by"] == "tester"
    assert meta["channel"] == "gateway_api"
    assert meta["workflow_manifest"]["workflow_kind"] == "code_change"
    # The Cody objective carries the shared demo_factory command with the fixed
    # full-land flag set + the directed slug.
    desc = row["description"]
    assert "--skill-tier library" in desc
    assert "--cody-mode hybrid" in desc
    assert f"--slug directed-{res['slug']}" in desc
    assert "DEMO ENGINE OVERRIDE" in desc


def test_intake_is_idempotent_on_repeated_seed(conn, monkeypatch):
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    r1 = ddb.queue_directed_demo_build("build a demo of foo", conn=conn)
    r2 = ddb.queue_directed_demo_build("build a demo of   FOO", conn=conn)
    assert r1["task_id"] == r2["task_id"]  # normalized dedup → same row
    rows = conn.execute(
        "SELECT COUNT(*) FROM task_hub_items WHERE source_kind='directed_build'"
    ).fetchone()
    assert int(rows[0]) == 1


def test_intake_skips_preference_judge(conn, monkeypatch):
    # Operator direction IS the judgment: the directed lane must NOT consult the
    # preference/buildability gate that the proactive queue path uses. Force the
    # gate to always-block and prove the directed row is still created.
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    from universal_agent.services import proactive_preferences

    monkeypatch.setattr(
        proactive_preferences,
        "should_block_proactive_task",
        lambda *a, **k: (True, "blocked-by-preference"),
    )
    res = ddb.queue_directed_demo_build("build a demo of anything", conn=conn)
    assert res["status"] == "queued"
    assert task_hub.get_item(conn, res["task_id"]) is not None


def test_intake_detects_url_seed(conn, monkeypatch):
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    res = ddb.queue_directed_demo_build("https://youtu.be/abc123", conn=conn)
    assert res["status"] == "queued"
    row = task_hub.get_item(conn, res["task_id"])
    meta = row.get("metadata") or {}
    assert meta["seed_url"] == "https://youtu.be/abc123"
    assert "--seed-url https://youtu.be/abc123" in row["description"]


# ---------------------------------------------------------------------------
# Dispatcher: classification, separate cap, priority, budget independence
# ---------------------------------------------------------------------------


def test_directed_build_classifies_to_coder_lane():
    d = pd.classify_task({"task_id": "d1", "source_kind": "directed_build"})
    assert d.agent_id == AGENT_CODER
    assert d.is_vp is True
    assert d.rule == "coding_to_codie"


def _seed_directed(conn, task_id):
    return ddb.queue_directed_demo_build(
        f"build a demo of {task_id}", conn=conn
    )  # relies on the caller having enabled the flag


@pytest.mark.asyncio
async def test_directed_build_dispatches_under_its_own_cap(conn, monkeypatch):
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    monkeypatch.setenv("UA_DIRECTED_DEMO_DAILY_CAP", "1")
    r = _seed_directed(conn, "alpha")
    item = task_hub.get_item(conn, r["task_id"])
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )
    assert len(calls) == 1
    assert calls[0]["vp_id"] == AGENT_CODER
    assert decisions[0].dispatched is True


@pytest.mark.asyncio
async def test_directed_defers_when_lane_disabled(conn, monkeypatch):
    # Row exists but the master flag is OFF → effective cap 0 → defer, never dispatch.
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    monkeypatch.setenv("UA_DIRECTED_DEMO_DAILY_CAP", "5")
    r = _seed_directed(conn, "beta")
    item = task_hub.get_item(conn, r["task_id"])
    # Now disable the lane before dispatch.
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "0")
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )
    assert calls == []
    assert decisions[0].deferred is True
    assert decisions[0].dispatched is False


@pytest.mark.asyncio
async def test_directed_defers_at_its_cap_boundary(conn, monkeypatch):
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    monkeypatch.setenv("UA_DIRECTED_DEMO_DAILY_CAP", "0")  # cap exhausted
    r = _seed_directed(conn, "gamma")
    item = task_hub.get_item(conn, r["task_id"])
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )
    assert calls == []
    assert decisions[0].deferred is True


@pytest.mark.asyncio
async def test_directed_dispatches_before_tutorial_for_the_single_slot(conn, monkeypatch):
    # Both are coder-lane P0 sharing the ONE coder slot; the operator-directed
    # build must win it this cycle and the tutorial_build defers.
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    monkeypatch.setenv("UA_DIRECTED_DEMO_DAILY_CAP", "5")
    monkeypatch.setenv("UA_PROACTIVE_DEMO_DAILY_CAP", "5")
    r = _seed_directed(conn, "delta")
    directed_item = task_hub.get_item(conn, r["task_id"])
    task_hub.upsert_item(
        conn,
        {
            "task_id": "tut-1",
            "source_kind": "tutorial_build",
            "title": "tut-1",
            "description": "tutorial build",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "metadata": {"video_title": "a tutorial"},
        },
    )
    tutorial_item = task_hub.get_item(conn, "tut-1")
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [tutorial_item, directed_item], active_assignments=[], dispatch_fn=stub
    )
    # Exactly one dispatch (single coder slot), and it is the directed build.
    assert len(calls) == 1
    assert calls[0]["task_id"] == r["task_id"]
    by_id = {d.task_id: d for d in decisions}
    assert by_id[r["task_id"]].dispatched is True
    assert by_id["tut-1"].deferred is True


@pytest.mark.asyncio
async def test_directed_budget_is_independent_of_tutorial_cap(conn, monkeypatch):
    # tutorial cap 0 (proactive builds all defer) must NOT block a directed build,
    # which has its own budget.
    monkeypatch.setenv("UA_DIRECTED_DEMO_ENABLED", "1")
    monkeypatch.setenv("UA_DIRECTED_DEMO_DAILY_CAP", "1")
    monkeypatch.setenv("UA_PROACTIVE_DEMO_DAILY_CAP", "0")
    r = _seed_directed(conn, "epsilon")
    item = task_hub.get_item(conn, r["task_id"])
    stub, calls = _make_stub()

    decisions = await pd.dispatch_claimed(
        conn, [item], active_assignments=[], dispatch_fn=stub
    )
    assert len(calls) == 1
    assert decisions[0].dispatched is True


# ---------------------------------------------------------------------------
# Enum memberships: worker finalize / completion gate parity with tutorial_build
# ---------------------------------------------------------------------------


def test_directed_build_in_all_demo_lane_enums():
    from universal_agent.services.self_briefing import GOAL_ELIGIBLE_SOURCE_KINDS
    from universal_agent.task_hub import DEMO_LANE_COMPLETION_GATED_SOURCE_KINDS
    from universal_agent.tools.vp_orchestration import _CODER_LANE_SOURCE_KINDS
    from universal_agent.vp.clients.claude_cli_client import (
        _WORKER_LOOP_FINALIZED_SOURCE_KINDS,
    )

    assert "directed_build" in _CODER_LANE_SOURCE_KINDS
    assert "directed_build" in pd.CODER_LANE_SOURCE_KINDS
    assert "directed_build" in GOAL_ELIGIBLE_SOURCE_KINDS
    assert "directed_build" in DEMO_LANE_COMPLETION_GATED_SOURCE_KINDS
    assert "directed_build" in _WORKER_LOOP_FINALIZED_SOURCE_KINDS
