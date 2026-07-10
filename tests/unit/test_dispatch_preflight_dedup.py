"""Tests for the dispatch pre-flight dedup gate (2026-07-10 reliability fix).

Verifies the three suppression conditions + a negative + the cancel nuance, at
both the helper level (``dispatch_preflight_dedup_skip`` and its parts) and the
integrated ``claim_next_dispatch_tasks`` path.

Conditions (OR-gate, any one suppresses re-dispatch):
  (a) a NON-CANCELLED in-flight/queued VP mission exists for the task,
  (b) a ``vp_mission_failure`` rescue is pending for the task, or
  (c) a completed mission already landed a demo (``demo_finalize.ok`` or
      ``vp_terminal_status=completed`` on the source task).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from universal_agent import task_hub

# ---------------------------------------------------------------------------
# Fixtures / seed helpers
# ---------------------------------------------------------------------------


def _th_conn() -> sqlite3.Connection:
    """In-memory task_hub (activity_state.db) schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _vp_conn() -> sqlite3.Connection:
    """In-memory vp_state.db schema (plain connect → FK off, so vp_missions
    rows can be seeded without a vp_sessions parent)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vp_missions (
          mission_id TEXT PRIMARY KEY,
          vp_id TEXT NOT NULL,
          status TEXT NOT NULL,
          objective TEXT NOT NULL DEFAULT '',
          payload_json TEXT,
          cancel_requested INTEGER DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT '',
          priority_tier TEXT NOT NULL DEFAULT 'background'
        )
        """
    )
    return conn


def _seed_vp_mission(
    vp_conn: sqlite3.Connection,
    *,
    mission_id: str,
    task_id: str,
    status: str,
    cancel_requested: int = 0,
    link_field: str = "task_id",
) -> None:
    """Seed one vp_missions row linked to ``task_id`` via ``link_field``.

    ``link_field`` is one of ``task_id`` (top-level payload), ``metadata_task_id``
    (payload.metadata.task_id), or ``metadata_linked_task_id``
    (payload.metadata.linked_task_id) — exercising every linkage field the
    gate's query checks.
    """
    if link_field == "task_id":
        payload = {"task_id": task_id}
    elif link_field == "metadata_task_id":
        payload = {"metadata": {"task_id": task_id}}
    elif link_field == "metadata_linked_task_id":
        payload = {"metadata": {"linked_task_id": task_id}}
    else:  # pragma: no cover - defensive
        raise ValueError(link_field)
    vp_conn.execute(
        """
        INSERT INTO vp_missions
            (mission_id, vp_id, status, objective, payload_json,
             cancel_requested, created_at, updated_at, priority_tier)
        VALUES (?, ?, ?, '', ?, ?, '', '', 'background')
        """,
        (
            mission_id,
            "vp.coder.primary",
            status,
            json.dumps(payload),
            cancel_requested,
        ),
    )
    vp_conn.commit()


def _seed_open_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    source_kind: str = "tutorial_build",
    metadata: dict | None = None,
) -> None:
    """Seed an agent-ready OPEN task eligible for the dispatch queue."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "source_ref": "test",
            "title": f"Task {task_id}",
            "description": "needs handling",
            "project_key": "immediate",
            "priority": 5,
            "labels": ["agent-ready", "must-complete"],
            "status": task_hub.TASK_STATUS_OPEN,
            "must_complete": True,
            "agent_ready": True,
            "metadata": metadata or {},
        },
    )


# ---------------------------------------------------------------------------
# Condition (c) — landed demo
# ---------------------------------------------------------------------------


def test_condition_c_demo_finalize_ok_suppresses() -> None:
    skip, reason = task_hub._dispatch_dedup_has_landed_demo(
        {"metadata": {"demo_finalize": {"ok": True}}}
    )
    assert skip is True
    assert "demo_finalize" in reason


def test_condition_c_vp_terminal_status_completed_suppresses() -> None:
    skip, reason = task_hub._dispatch_dedup_has_landed_demo(
        {"metadata": {"vp_terminal_status": "completed"}}
    )
    assert skip is True
    assert "vp_terminal_status" in reason


def test_condition_c_vp_terminal_status_completed_nested_under_delegation() -> None:
    """backfill_vp_cancel_bookkeeping stamps vp_terminal_status under delegation."""
    skip, _ = task_hub._dispatch_dedup_has_landed_demo(
        {"metadata": {"delegation": {"vp_terminal_status": "Completed"}}}
    )
    assert skip is True


def test_condition_c_no_demo_does_not_suppress() -> None:
    skip, _ = task_hub._dispatch_dedup_has_landed_demo(
        {"metadata": {"demo_finalize": {"ok": False}, "vp_terminal_status": "failed"}}
    )
    assert skip is False


# ---------------------------------------------------------------------------
# Condition (b) — pending vp_failure rescue
# ---------------------------------------------------------------------------


def test_condition_b_pending_rescue_suppresses() -> None:
    conn = _th_conn()
    try:
        # A non-terminal rescue pointing at task T.
        task_hub.upsert_item(
            conn,
            {
                "task_id": "vp_failure:vp-mission-xyz",
                "source_kind": "vp_mission_failure",
                "title": "rescue",
                "description": "fix it",
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata": {"original_task_id": "task_T"},
            },
        )
        skip, reason = task_hub._dispatch_dedup_has_pending_rescue(conn, "task_T")
        assert skip is True
        assert "rescue" in reason
    finally:
        conn.close()


def test_condition_b_terminal_rescue_does_not_suppress() -> None:
    """A completed/parked rescue is no longer pending — slot should free."""
    conn = _th_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "vp_failure:vp-mission-xyz",
                "source_kind": "vp_mission_failure",
                "title": "rescue",
                "description": "fix it",
                "status": task_hub.TASK_STATUS_COMPLETED,
                "metadata": {"original_task_id": "task_T"},
            },
        )
        skip, _ = task_hub._dispatch_dedup_has_pending_rescue(conn, "task_T")
        assert skip is False
    finally:
        conn.close()


def test_condition_b_rescue_via_mission_linkage_suppresses() -> None:
    """Rescues do NOT reliably carry original_task_id (NULL on all 5 rescues
    2026-07-07), so condition (b) must ALSO link via the rescue's mission_id
    (``vp_failure:{mid}``) -> ``vp_missions`` payload -> source task. This is
    the path that catches the real Nemotron-class rescues."""
    conn = _th_conn()
    vp_conn = _vp_conn()
    try:
        # Pending rescue with NO original_task_id, task_id = vp_failure:{mid}.
        task_hub.upsert_item(
            conn,
            {
                "task_id": "vp_failure:vp-mission-linked",
                "source_kind": "vp_mission_failure",
                "title": "rescue",
                "description": "fix it",
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata": {},  # deliberately no original_task_id
            },
        )
        # The failing mission (terminal) linked to task_T via payload.task_id.
        _seed_vp_mission(
            vp_conn,
            mission_id="vp-mission-linked",
            task_id="task_T",
            status="failed",
            cancel_requested=0,
        )
        skip, reason = task_hub._dispatch_dedup_has_pending_rescue(
            conn, "task_T", vp_conn=vp_conn
        )
        assert skip is True
        assert "mission-linkage" in reason
    finally:
        conn.close()
        vp_conn.close()


def test_condition_b_rescue_without_vp_conn_falls_back_to_direct_only() -> None:
    """When vp_state.db is unreachable, the mission-linkage path is skipped —
    only the direct original_task_id path runs. A rescue with neither signal
    does not suppress (best-effort: never block the claim on a missing db)."""
    conn = _th_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "vp_failure:vp-mission-orphan",
                "source_kind": "vp_mission_failure",
                "title": "rescue",
                "description": "fix it",
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata": {},  # no original_task_id, no vp_conn -> cannot link
            },
        )
        skip, _ = task_hub._dispatch_dedup_has_pending_rescue(
            conn, "task_T", vp_conn=None
        )
        assert skip is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Condition (a) — non-cancelled in-flight VP mission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize(
    "link_field",
    ["task_id", "metadata_task_id", "metadata_linked_task_id"],
)
def test_condition_a_inflight_mission_suppresses(status: str, link_field: str) -> None:
    vp_conn = _vp_conn()
    try:
        _seed_vp_mission(
            vp_conn,
            mission_id="vp-mission-abc",
            task_id="task_T",
            status=status,
            cancel_requested=0,
            link_field=link_field,
        )
        skip, reason = task_hub._dispatch_dedup_has_inflight_mission(vp_conn, "task_T")
        assert skip is True
        assert "in-flight" in reason
    finally:
        vp_conn.close()


def test_condition_a_fully_cancelled_mission_does_not_suppress() -> None:
    """A fully-cancelled mission (status='cancelled') must free the slot."""
    vp_conn = _vp_conn()
    try:
        _seed_vp_mission(
            vp_conn,
            mission_id="vp-mission-cancelled",
            task_id="task_T",
            status="cancelled",
            cancel_requested=1,
        )
        skip, _ = task_hub._dispatch_dedup_has_inflight_mission(vp_conn, "task_T")
        assert skip is False
    finally:
        vp_conn.close()


def test_condition_a_cancel_requested_pending_does_not_suppress() -> None:
    """Cancel-requested but not yet finalized (status still running) must free
    the slot — otherwise a slow-to-finalize cancel blocks forever."""
    vp_conn = _vp_conn()
    try:
        _seed_vp_mission(
            vp_conn,
            mission_id="vp-mission-cancelreq",
            task_id="task_T",
            status="running",
            cancel_requested=1,
        )
        skip, _ = task_hub._dispatch_dedup_has_inflight_mission(vp_conn, "task_T")
        assert skip is False
    finally:
        vp_conn.close()


def test_condition_a_terminal_completed_mission_does_not_suppress() -> None:
    """A completed mission alone (no demo_finalize/vp_terminal on the task)
    does not trigger condition (a) — that's condition (c)'s job on the task
    row. Condition (a) keys on in-flight status only."""
    vp_conn = _vp_conn()
    try:
        _seed_vp_mission(
            vp_conn,
            mission_id="vp-mission-done",
            task_id="task_T",
            status="completed",
            cancel_requested=0,
        )
        skip, _ = task_hub._dispatch_dedup_has_inflight_mission(vp_conn, "task_T")
        assert skip is False
    finally:
        vp_conn.close()


# ---------------------------------------------------------------------------
# Orchestrator (OR-gate) + negative
# ---------------------------------------------------------------------------


def test_orchestrator_negative_proceeds() -> None:
    """No demo, no rescue, no in-flight mission → dispatch proceeds."""
    conn = _th_conn()
    vp_conn = _vp_conn()
    try:
        _seed_open_task(conn, "task_T", metadata={})
        item = task_hub.get_item(conn, "task_T")
        skip, reason = task_hub.dispatch_preflight_dedup_skip(
            conn, "task_T", item=item, vp_conn=vp_conn
        )
        assert skip is False
        assert reason == ""
    finally:
        conn.close()
        vp_conn.close()


def test_orchestrator_condition_c_short_circuits_without_vp_conn() -> None:
    """A landed demo suppresses even when vp_state.db is unreachable (vp_conn
    None and best-effort open fails) — conditions (b)/(c) must still apply."""
    conn = _th_conn()
    try:
        _seed_open_task(
            conn, "task_T", metadata={"demo_finalize": {"ok": True}}
        )
        item = task_hub.get_item(conn, "task_T")
        # Point UA_VP_DB_PATH at a non-existent dir so the best-effort open
        # fails inside the orchestrator; (c) must still fire.
        import os

        old = os.environ.get("UA_VP_DB_PATH")
        os.environ["UA_VP_DB_PATH"] = "/no/such/dir/vp.db"
        try:
            skip, reason = task_hub.dispatch_preflight_dedup_skip(
                conn, "task_T", item=item
            )
        finally:
            if old is None:
                os.environ.pop("UA_VP_DB_PATH", None)
            else:
                os.environ["UA_VP_DB_PATH"] = old
        assert skip is True
        assert "demo_finalize" in reason
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Integrated claim path — proves the gate is wired into the seizure loop
# ---------------------------------------------------------------------------


def test_claim_skips_task_with_landed_demo_condition_c() -> None:
    """A task whose demo already landed is NOT claimed (a fresh task behind it
    is). Condition (c) is a pure task_hub query — no vp db needed."""
    conn = _th_conn()
    try:
        _seed_open_task(
            conn, "task:landed", metadata={"demo_finalize": {"ok": True}}
        )
        _seed_open_task(conn, "task:fresh", metadata={})
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=5, agent_id="test")
        claimed_ids = {c["task_id"] for c in claimed}
        assert "task:landed" not in claimed_ids
        assert "task:fresh" in claimed_ids
    finally:
        conn.close()


def test_claim_skips_task_with_pending_rescue_condition_b() -> None:
    conn = _th_conn()
    try:
        _seed_open_task(conn, "task:rescued", metadata={})
        task_hub.upsert_item(
            conn,
            {
                "task_id": "vp_failure:vp-mission-r",
                "source_kind": "vp_mission_failure",
                "title": "rescue",
                "description": "fix it",
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata": {"original_task_id": "task:rescued"},
            },
        )
        _seed_open_task(conn, "task:fresh", metadata={})
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=5, agent_id="test")
        claimed_ids = {c["task_id"] for c in claimed}
        assert "task:rescued" not in claimed_ids
        assert "task:fresh" in claimed_ids
    finally:
        conn.close()


def test_claim_skips_task_with_inflight_mission_condition_a(
    tmp_path, monkeypatch
) -> None:
    """End-to-end condition (a): the claim path consults vp_state.db and skips
    a task that has a non-cancelled in-flight mission."""
    vp_db = tmp_path / "vp.db"
    # Seed the vp db file (plain connect, FK off) with a running mission
    # linked to task:a via the top-level payload task_id.
    seed = sqlite3.connect(str(vp_db))
    seed.execute(
        """
        CREATE TABLE vp_missions (
          mission_id TEXT PRIMARY KEY,
          vp_id TEXT NOT NULL,
          status TEXT NOT NULL,
          objective TEXT NOT NULL DEFAULT '',
          payload_json TEXT,
          cancel_requested INTEGER DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT '',
          priority_tier TEXT NOT NULL DEFAULT 'background'
        )
        """
    )
    seed.execute(
        """
        INSERT INTO vp_missions
            (mission_id, vp_id, status, objective, payload_json,
             cancel_requested, created_at, updated_at, priority_tier)
        VALUES (?, ?, ?, '', ?, 0, '', '', 'background')
        """,
        (
            "vp-mission-inflight",
            "vp.coder.primary",
            "running",
            json.dumps({"task_id": "task:inflight"}),
        ),
    )
    seed.commit()
    seed.close()

    monkeypatch.setenv("UA_VP_DB_PATH", str(vp_db))

    conn = _th_conn()
    try:
        _seed_open_task(conn, "task:inflight", metadata={})
        _seed_open_task(conn, "task:fresh", metadata={})
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=5, agent_id="test")
        claimed_ids = {c["task_id"] for c in claimed}
        assert "task:inflight" not in claimed_ids
        assert "task:fresh" in claimed_ids
    finally:
        conn.close()


def test_claim_allows_task_with_only_cancelled_mission_cancel_nuance(
    tmp_path, monkeypatch
) -> None:
    """The cancel nuance, end-to-end: a task whose only mission is fully
    cancelled IS claimable (the slot freed)."""
    vp_db = tmp_path / "vp.db"
    seed = sqlite3.connect(str(vp_db))
    seed.execute(
        """
        CREATE TABLE vp_missions (
          mission_id TEXT PRIMARY KEY,
          vp_id TEXT NOT NULL,
          status TEXT NOT NULL,
          objective TEXT NOT NULL DEFAULT '',
          payload_json TEXT,
          cancel_requested INTEGER DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT '',
          priority_tier TEXT NOT NULL DEFAULT 'background'
        )
        """
    )
    seed.execute(
        """
        INSERT INTO vp_missions
            (mission_id, vp_id, status, objective, payload_json,
             cancel_requested, created_at, updated_at, priority_tier)
        VALUES (?, ?, ?, '', ?, 1, '', '', 'background')
        """,
        (
            "vp-mission-cancelled",
            "vp.coder.primary",
            "cancelled",
            json.dumps({"task_id": "task:freed"}),
        ),
    )
    seed.commit()
    seed.close()

    monkeypatch.setenv("UA_VP_DB_PATH", str(vp_db))

    conn = _th_conn()
    try:
        _seed_open_task(conn, "task:freed", metadata={})
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=5, agent_id="test")
        claimed_ids = {c["task_id"] for c in claimed}
        assert "task:freed" in claimed_ids
    finally:
        conn.close()
