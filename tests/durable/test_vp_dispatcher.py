import sqlite3

import pytest

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    claim_next_vp_mission,
    finalize_vp_mission,
    get_vp_mission,
    queue_vp_mission,
    request_vp_mission_cancel,
)
from universal_agent.vp.dispatcher import MissionDispatchRequest, dispatch_mission


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    ensure_schema(conn)
    return conn


def test_vp_mission_queue_claim_finalize():
    conn = _conn()
    queue_vp_mission(
        conn=conn,
        mission_id="mission-1",
        vp_id="vp.general.primary",
        mission_type="task",
        objective="Do the thing",
        payload={"a": 1},
        priority=10,
    )

    claimed = claim_next_vp_mission(
        conn=conn,
        vp_id="vp.general.primary",
        worker_id="worker-1",
        lease_ttl_seconds=60,
    )
    assert claimed is not None
    assert claimed["mission_id"] == "mission-1"
    assert claimed["status"] == "running"
    assert claimed["worker_id"] == "worker-1"

    ok = finalize_vp_mission(conn, "mission-1", "completed", result_ref="workspace:///tmp/test")
    assert ok is True
    row = get_vp_mission(conn, "mission-1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["result_ref"] == "workspace:///tmp/test"


def test_vp_mission_cancel_request():
    conn = _conn()
    queue_vp_mission(
        conn=conn,
        mission_id="mission-cancel",
        vp_id="vp.general.primary",
        mission_type="task",
        objective="cancel me",
        payload={},
    )
    assert request_vp_mission_cancel(conn, "mission-cancel") is True
    row = get_vp_mission(conn, "mission-cancel")
    assert row is not None
    assert int(row["cancel_requested"] or 0) == 1


def test_dispatch_mission_idempotent_key_reuses_mission():
    conn = _conn()
    request = MissionDispatchRequest(
        vp_id="vp.general.primary",
        mission_type="task",
        objective="repeatable objective",
        constraints={},
        budget={},
        idempotency_key="same-key",
        source_session_id="session-a",
        source_turn_id="turn-1",
        reply_mode="async",
        priority=100,
    )
    first = dispatch_mission(conn, request)
    second = dispatch_mission(conn, request)
    assert first["mission_id"] == second["mission_id"]


def test_dispatch_coder_blocks_ua_repo_target(monkeypatch):
    conn = _conn()
    monkeypatch.setenv("UA_VP_HARD_BLOCK_UA_REPO", "1")
    with pytest.raises(ValueError):
        dispatch_mission(
            conn,
            MissionDispatchRequest(
                vp_id="vp.coder.primary",
                mission_type="coding_task",
                objective="touch the UA repo",
                constraints={"target_path": "."},
                budget={},
                idempotency_key="",
                source_session_id="session-a",
                source_turn_id="turn-1",
                reply_mode="async",
                priority=100,
            ),
        )
