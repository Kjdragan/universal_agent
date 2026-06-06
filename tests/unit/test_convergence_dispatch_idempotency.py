"""Idempotency backstop for convergence-candidate dispatch (PR2 — change C).

A convergence candidate (e.g. a cross-channel ATLAS intel-brief cluster) maps
deterministically to a Task Hub task (``convergence-candidate:<hash>``) and,
once dispatched, a ``vp_missions`` row. The startup reconciler bug could
false-orphan the mission's mirror row, reopen the source task, and let the CSI
sweep re-queue a SECOND ATLAS authoring run for the same candidate — a
duplicate mission. ``_inflight_vp_mission_for_candidate`` is the durable
backstop: ``write_convergence_candidate`` skips the (re)queue when a
``vp_missions`` row for THIS candidate is already in status queued/running.

These tests pin the backstop with the LLM triage disabled (legacy always-queue
path) so they stay fully offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

from universal_agent import task_hub
from universal_agent.services import proactive_convergence as pc


# vp_missions schema mirrors durable/migrations.py CREATE TABLE vp_missions.
_VP_MISSIONS_DDL = """
CREATE TABLE vp_missions (
  mission_id TEXT PRIMARY KEY,
  vp_id TEXT NOT NULL,
  run_id TEXT,
  status TEXT NOT NULL,
  mission_type TEXT,
  objective TEXT NOT NULL,
  budget_json TEXT,
  payload_json TEXT,
  result_ref TEXT,
  priority INTEGER DEFAULT 100,
  priority_tier TEXT NOT NULL DEFAULT 'background',
  worker_id TEXT,
  claim_expires_at TEXT,
  cancel_requested INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL
)
"""


_SIGNATURES = [
    {
        "video_id": "vid_aaa",
        "channel_id": "chan_1",
        "channel_name": "Alpha Channel",
        "video_title": "Agents converge",
        "video_url": "https://example.test/aaa",
        "primary_topics": ["agents"],
        "secondary_topics": [],
        "key_claims": ["claim a"],
    },
    {
        "video_id": "vid_bbb",
        "channel_id": "chan_2",
        "channel_name": "Beta Channel",
        "video_title": "Agents everywhere",
        "video_url": "https://example.test/bbb",
        "primary_topics": ["agents"],
        "secondary_topics": [],
        "key_claims": ["claim b"],
    },
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    pc.ensure_schema(conn)
    return conn


def _expected_ids() -> tuple[str, str]:
    """Recompute the deterministic candidate_id + task_id the same way
    ``write_convergence_candidate`` does, so we can pre-seed a matching
    in-flight mission BEFORE the first call."""
    video_ids = sorted(
        {str(s["video_id"]).strip() for s in _SIGNATURES if str(s["video_id"]).strip()}
    )
    seed = "|".join(video_ids)
    candidate_id = f"cand_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"
    task_id = f"convergence-candidate:{candidate_id.removeprefix('cand_')}"
    return candidate_id, task_id


def _insert_running_mission(conn: sqlite3.Connection, *, task_id: str) -> str:
    """Insert a status='running' vp_mission linked to the candidate task via the
    confirmed payload linkage (payload.task_id + idempotency_key 'task-<id>')."""
    conn.execute(_VP_MISSIONS_DDL)
    now_iso = datetime.now(timezone.utc).isoformat()
    lease = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    mission_id = "vp-mission-inflight01"
    payload = (
        '{"task_id": "%s", "idempotency_key": "task-%s", '
        '"source_session_id": "internal.vp_tool"}' % (task_id, task_id)
    )
    conn.execute(
        """
        INSERT INTO vp_missions (
            mission_id, vp_id, run_id, status, mission_type, objective,
            budget_json, payload_json, result_ref, priority, priority_tier,
            worker_id, claim_expires_at, cancel_requested, created_at,
            started_at, completed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id, "vp.general.primary", None, "running", "intel_brief",
            "author intel brief", None, payload, None, 100, "background",
            "vp.general.primary.worker.x", lease, 0, now_iso, now_iso, None, now_iso,
        ),
    )
    conn.commit()
    return mission_id


def test_second_dispatch_skipped_when_mission_in_flight(monkeypatch):
    """With an in-flight (running) vp_mission for the candidate already present,
    write_convergence_candidate must NOT queue a (duplicate) Task Hub task."""
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "0")  # legacy always-queue path
    conn = _conn()
    _candidate_id, task_id = _expected_ids()
    _insert_running_mission(conn, task_id=task_id)

    row = pc.write_convergence_candidate(conn, signatures=_SIGNATURES)

    assert row["_newly_queued"] is False, "must not re-queue when a mission is in flight"
    assert task_hub.get_item(conn, task_id) is None, (
        "no duplicate Task Hub item should be created for an in-flight candidate"
    )


def test_first_dispatch_queues_then_second_is_idempotent(monkeypatch):
    """End-to-end: first call queues the task; after we mark a vp_mission running
    for that candidate, the second call is a no-op (no duplicate)."""
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "0")
    conn = _conn()
    _candidate_id, task_id = _expected_ids()

    # First sweep: no mission yet -> a task is queued.
    first = pc.write_convergence_candidate(conn, signatures=_SIGNATURES)
    assert first["_newly_queued"] is True
    item_after_first = task_hub.get_item(conn, task_id)
    assert item_after_first is not None
    assert item_after_first["status"] == task_hub.TASK_STATUS_OPEN

    # Dispatcher claims it: a running vp_mission now exists for the candidate.
    _insert_running_mission(conn, task_id=task_id)

    # Second sweep (e.g. after a false-orphan reopened the row): must NOT
    # create/queue a second mission card.
    second = pc.write_convergence_candidate(conn, signatures=_SIGNATURES)
    assert second["_newly_queued"] is False, "re-queue must be skipped — mission in flight"


def test_dispatch_queues_when_no_mission_table(monkeypatch):
    """Legacy/test DBs without a vp_missions table: backstop is a safe no-op and
    the candidate is queued exactly as before (helper returns None)."""
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "0")
    conn = _conn()  # no vp_missions table created
    _candidate_id, task_id = _expected_ids()

    row = pc.write_convergence_candidate(conn, signatures=_SIGNATURES)

    assert row["_newly_queued"] is True
    assert task_hub.get_item(conn, task_id) is not None


def test_completed_mission_does_not_block_requeue(monkeypatch):
    """A terminal (completed) mission must NOT block a fresh queue — only
    in-flight (queued/running) missions short-circuit the dispatch."""
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "0")
    conn = _conn()
    _candidate_id, task_id = _expected_ids()

    # Insert a COMPLETED mission for this candidate.
    conn.execute(_VP_MISSIONS_DDL)
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = '{"task_id": "%s", "idempotency_key": "task-%s"}' % (task_id, task_id)
    conn.execute(
        """
        INSERT INTO vp_missions (
            mission_id, vp_id, run_id, status, mission_type, objective,
            budget_json, payload_json, result_ref, priority, priority_tier,
            worker_id, claim_expires_at, cancel_requested, created_at,
            started_at, completed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "vp-mission-done01", "vp.general.primary", None, "completed",
            "intel_brief", "author intel brief", None, payload, None, 100,
            "background", "w", None, 0, now_iso, now_iso, now_iso, now_iso,
        ),
    )
    conn.commit()

    row = pc.write_convergence_candidate(conn, signatures=_SIGNATURES)
    assert row["_newly_queued"] is True, "a completed mission must not block re-queue"
    assert task_hub.get_item(conn, task_id) is not None
