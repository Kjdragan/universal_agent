"""Regression tests for the cron-live grace window in reconcile_task_lifecycle.

Background: in-process LLM crons (e.g. paper_to_podcast_daily) execute inside the
daemon and carry no provider_session_id, so the lifecycle reconciler's session-id
liveness checks can never recognise them as alive. Before the grace window, an
on-demand reconcile triggered by a dashboard read would false-orphan a *running*
cron — flipping its assignment to failed and bouncing the task back to the
unassigned column — even though the work was still in flight.

These tests pin the fix: a young cron-owned assignment is protected on the
on-demand path (grace > 0) but still reaped at startup (grace == 0), an old cron
is reaped once it ages past the window, and non-cron tasks are unaffected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    # reconcile_task_lifecycle -> _email_side_effects_detected reads this table.
    conn.execute(
        "CREATE TABLE email_task_mappings (task_id TEXT PRIMARY KEY, email_sent_at TEXT)"
    )
    conn.commit()
    return conn


def _insert_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    source_kind: str,
    metadata_json: str,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO task_hub_items (
            task_id, source_kind, source_ref, title, description, project_key, priority,
            due_at, labels_json, status, must_complete, incident_key, workstream_id,
            subtask_role, parent_task_id, agent_ready, score, score_confidence, stale_state,
            seizure_state, mirror_status, metadata_json, created_at, updated_at, trigger_type,
            refinement_stage, refinement_history_json, completion_token
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id, source_kind, "", task_id, "", "immediate", 2, None, "[]",
            task_hub.TASK_STATUS_IN_PROGRESS, 0, None, None, None, None,
            0, 7.0, 0.8, "fresh", "seized", "internal", metadata_json,
            now_iso, now_iso, "immediate", None, "{}", None,
        ),
    )
    conn.commit()


def _insert_assignment(
    conn: sqlite3.Connection,
    *,
    assignment_id: str,
    task_id: str,
    started_at: str,
) -> None:
    # In-process cron assignments carry NO provider_session_id (they run inside
    # the daemon, not as a tracked execution session) — that's the whole reason
    # the session-id liveness check can't protect them.
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (assignment_id, task_id, "cron_scheduler", None, "running", started_at),
    )
    conn.commit()


_CRON_META = (
    '{"cron_owned": true, "system_job": "paper_to_podcast_daily", "job_id": "abc"}'
)


def _setup_cron(conn: sqlite3.Connection, *, age_seconds: float) -> str:
    started_at = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    _insert_task(
        conn,
        task_id="cron:paper_to_podcast_daily",
        source_kind="cron_run",
        metadata_json=_CRON_META,
    )
    _insert_assignment(
        conn,
        assignment_id="asg_cron_live",
        task_id="cron:paper_to_podcast_daily",
        started_at=started_at,
    )
    return "asg_cron_live"


def _assignment_state(conn: sqlite3.Connection, assignment_id: str) -> tuple[str, str]:
    row = conn.execute(
        "SELECT state, COALESCE(result_summary, '') FROM task_hub_assignments WHERE assignment_id = ?",
        (assignment_id,),
    ).fetchone()
    return str(row[0]), str(row[1])


def test_young_cron_protected_on_demand():
    """grace > 0: a 30s-old cron run is NOT reaped (it's still in flight)."""
    conn = _conn()
    asg = _setup_cron(conn, age_seconds=30)

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=3600,
    )

    state, _ = _assignment_state(conn, asg)
    item = task_hub.get_item(conn, "cron:paper_to_podcast_daily")
    assert state == "running", "live cron assignment must not be reaped within grace"
    assert item["status"] == task_hub.TASK_STATUS_IN_PROGRESS


def test_young_cron_reaped_at_startup():
    """grace == 0 (startup recovery): the same young cron IS reaped, because the
    process was dead and every in-progress cron is genuinely orphaned."""
    conn = _conn()
    asg = _setup_cron(conn, age_seconds=30)

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    state, summary = _assignment_state(conn, asg)
    item = task_hub.get_item(conn, "cron:paper_to_podcast_daily")
    assert state == "failed"
    assert summary == "reconciled_orphaned_assignment"
    assert item["status"] == task_hub.TASK_STATUS_OPEN


def test_old_cron_reaped_past_grace():
    """grace > 0 but the cron assignment is older than the window -> reaped.
    Guards against a genuinely-stuck cron lingering forever on the demand path."""
    conn = _conn()
    asg = _setup_cron(conn, age_seconds=7200)  # 2h old, grace 1h

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=3600,
    )

    state, summary = _assignment_state(conn, asg)
    assert state == "failed"
    assert summary == "reconciled_orphaned_assignment"


def test_non_cron_task_unaffected_by_grace():
    """The grace is cron-scoped: a young non-cron in-progress task with a dead
    session is still reaped even when grace > 0."""
    conn = _conn()
    _insert_task(
        conn,
        task_id="email:42",
        source_kind="email",
        metadata_json="{}",
    )
    _insert_assignment(
        conn,
        assignment_id="asg_email",
        task_id="email:42",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=3600,
    )

    state, summary = _assignment_state(conn, "asg_email")
    assert state == "failed", "non-cron tasks must not get the cron grace"
    assert summary == "reconciled_orphaned_assignment"


# ──────────────────────────────────────────────────────────────────────────
# VP-mission liveness guard (PR2 — _vp_mission_lease_live)
#
# A VP mission's Task Hub mirror row (keyed task_id == mission_id) is written
# in_progress with STATUS ONLY by vp/worker_loop — no provider_session_id and
# no assignment row. The session/assignment liveness checks therefore can never
# protect it, and at startup recovery (grace=0) the reconciler false-orphaned
# the (alive, heartbeating) mission with last_disposition_reason=
# "reconciled_orphaned_in_progress", reopening the source task and causing a
# DUPLICATE mission run. The fix consults the authoritative liveness signal in
# the vp_missions table: status='running' with a future claim_expires_at lease.
# ──────────────────────────────────────────────────────────────────────────

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


def _ensure_vp_missions_table(conn: sqlite3.Connection) -> None:
    conn.execute(_VP_MISSIONS_DDL)
    conn.commit()


def _insert_vp_mission(
    conn: sqlite3.Connection,
    *,
    mission_id: str,
    status: str,
    claim_expires_at: str | None,
    payload_json: str = "{}",
    vp_id: str = "vp.general.primary",
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
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
            mission_id, vp_id, None, status, "intel_brief", "author an intel brief",
            None, payload_json, None, 100, "background",
            f"{vp_id}.worker.abc", claim_expires_at, 0, now_iso,
            now_iso, None, now_iso,
        ),
    )
    conn.commit()


def _disposition_reason(conn: sqlite3.Connection, task_id: str) -> str:
    item = task_hub.get_item(conn, task_id) or {}
    dispatch = (item.get("metadata") or {}).get("dispatch") or {}
    return str(dispatch.get("last_disposition_reason") or "")


# The mirror row carries the producer handle metadata.dispatch.vp_mission_id.
# `agent_id` is cosmetic for the guard (it keys off the vp_missions lease, NOT
# vp_id) — it is parameterized so the Cody (vp.coder.primary) case reads true.
def _vp_mirror_meta(mission_id: str, agent_id: str = "vp.general.primary") -> str:
    return (
        '{"dispatch": {"vp_mission_id": "%s", '
        '"active_agent_id": "%s", '
        '"active_provider_session_id": "%s.worker.abc", '
        '"active_workspace_dir": "/tmp/ws"}}' % (mission_id, agent_id, agent_id)
    )


def test_vp_mission_live_lease_not_reaped_at_startup():
    """Case (1): a VP mission mirror row with a LIVE lease (status='running',
    claim_expires_at = now+300s) and no provider_session_id STAYS in_progress
    at startup recovery (grace=0) — no reconciled_orphaned_in_progress."""
    conn = _conn()
    _ensure_vp_missions_table(conn)
    mission_id = "vp-mission-live01"
    _insert_task(
        conn,
        task_id=mission_id,
        source_kind="vp_mission",
        metadata_json=_vp_mirror_meta(mission_id),
    )
    live_lease = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    _insert_vp_mission(
        conn, mission_id=mission_id, status="running", claim_expires_at=live_lease
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, mission_id)
    assert item["status"] == task_hub.TASK_STATUS_IN_PROGRESS, (
        "a live-lease VP mission must NOT be reaped at startup"
    )
    assert _disposition_reason(conn, mission_id) != "reconciled_orphaned_in_progress"


def test_vp_mission_expired_lease_reaped():
    """Case (2): a VP mission with an EXPIRED lease (claim_expires_at = now-60s)
    IS reaped/reopened — reaping keys off a real stale signal, not handles."""
    conn = _conn()
    _ensure_vp_missions_table(conn)
    mission_id = "vp-mission-dead01"
    _insert_task(
        conn,
        task_id=mission_id,
        source_kind="vp_mission",
        metadata_json=_vp_mirror_meta(mission_id),
    )
    expired_lease = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    _insert_vp_mission(
        conn, mission_id=mission_id, status="running", claim_expires_at=expired_lease
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, mission_id)
    assert item["status"] == task_hub.TASK_STATUS_OPEN, (
        "an expired-lease VP mission must be reaped/reopened"
    )
    assert _disposition_reason(conn, mission_id) == "reconciled_orphaned_in_progress"


def test_vp_mission_no_row_still_reaped():
    """Case (3): a VP-shaped mirror row with NO vp_missions row at all is still
    reaped — preserves current safety (no live signal -> orphan)."""
    conn = _conn()
    _ensure_vp_missions_table(conn)
    mission_id = "vp-mission-orphan01"
    _insert_task(
        conn,
        task_id=mission_id,
        source_kind="vp_mission",
        metadata_json=_vp_mirror_meta(mission_id),
    )
    # Intentionally NO _insert_vp_mission — the table exists but is empty.

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, mission_id)
    assert item["status"] == task_hub.TASK_STATUS_OPEN, (
        "a VP mirror row without any vp_missions row must still be reaped"
    )
    assert _disposition_reason(conn, mission_id) == "reconciled_orphaned_in_progress"


def test_vp_guard_safe_without_vp_missions_table():
    """The guard must be a no-op (and not raise) on legacy DBs that lack the
    vp_missions table — the VP-shaped row is simply reaped as before."""
    conn = _conn()  # no vp_missions table created
    mission_id = "vp-mission-legacy01"
    _insert_task(
        conn,
        task_id=mission_id,
        source_kind="vp_mission",
        metadata_json=_vp_mirror_meta(mission_id),
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, mission_id)
    assert item["status"] == task_hub.TASK_STATUS_OPEN
    assert _disposition_reason(conn, mission_id) == "reconciled_orphaned_in_progress"


def test_vp_candidate_task_live_lease_not_reaped():
    """Candidate linkage: when the in_progress row is the convergence-candidate
    task itself (dispatch interrupted before the delegate transition), the guard
    maps task_id -> vp_missions via payload idempotency_key and protects it."""
    conn = _conn()
    _ensure_vp_missions_table(conn)
    candidate_task_id = "convergence-candidate:6d11787ce2116856"
    mission_id = "vp-mission-cand01"
    # The dispatch payload links the mission back to the candidate task:
    #   payload.task_id == candidate_task_id and
    #   idempotency_key == "task-<candidate_task_id>" (CONTAINS the task_id).
    payload = (
        '{"task_id": "%s", '
        '"idempotency_key": "task-%s", '
        '"source_session_id": "internal.vp_tool"}'
        % (candidate_task_id, candidate_task_id)
    )
    _insert_task(
        conn,
        task_id=candidate_task_id,
        source_kind="convergence_candidate",
        metadata_json="{}",  # candidate row carries no dispatch handle
    )
    live_lease = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    _insert_vp_mission(
        conn,
        mission_id=mission_id,
        status="running",
        claim_expires_at=live_lease,
        payload_json=payload,
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, candidate_task_id)
    assert item["status"] == task_hub.TASK_STATUS_IN_PROGRESS, (
        "a candidate task with a live linked mission must not be reaped"
    )
    assert _disposition_reason(conn, candidate_task_id) != "reconciled_orphaned_in_progress"


def test_non_vp_in_progress_row_reaped_with_vp_table_present():
    """Non-VP behavior unchanged: an in_progress row with no vp_missions match
    is still reaped even when the vp_missions table exists and is non-empty."""
    conn = _conn()
    _ensure_vp_missions_table(conn)
    # Unrelated live mission so the table is non-empty.
    _insert_vp_mission(
        conn,
        mission_id="vp-mission-unrelated",
        status="running",
        claim_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(),
        payload_json='{"task_id": "convergence-candidate:deadbeef"}',
    )
    _insert_task(
        conn,
        task_id="email:99",
        source_kind="email",
        metadata_json="{}",
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, "email:99")
    assert item["status"] == task_hub.TASK_STATUS_OPEN
    assert _disposition_reason(conn, "email:99") == "reconciled_orphaned_in_progress"


# The guard is AGENT-AGNOSTIC: it keys off the vp_missions lease, never off
# vp_id. Cody (vp.coder.primary, the code-building VP) runs through the same
# VpWorkerLoop + vp_missions lease as Atlas (vp.general.primary), so it was
# equally vulnerable to the false-orphan reaping and is equally protected. These
# cases pin that — a regression that special-cased Atlas would break them.
def test_vp_coder_mission_live_lease_not_reaped_at_startup():
    """A live Cody (vp.coder.primary) mission is protected identically to Atlas:
    a fresh vp_missions lease keeps the mirror row in_progress at startup."""
    conn = _conn()
    _ensure_vp_missions_table(conn)
    mission_id = "vp-mission-coder-live01"
    _insert_task(
        conn,
        task_id=mission_id,
        source_kind="vp_mission",
        metadata_json=_vp_mirror_meta(mission_id, agent_id="vp.coder.primary"),
    )
    live_lease = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    _insert_vp_mission(
        conn,
        mission_id=mission_id,
        status="running",
        claim_expires_at=live_lease,
        vp_id="vp.coder.primary",
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, mission_id)
    assert item["status"] == task_hub.TASK_STATUS_IN_PROGRESS, (
        "a live-lease Cody mission must NOT be reaped at startup"
    )
    assert _disposition_reason(conn, mission_id) != "reconciled_orphaned_in_progress"


def test_vp_coder_mission_expired_lease_reaped():
    """A Cody mission with an EXPIRED lease IS reaped — reaping keys off the
    stale lease, not the agent type."""
    conn = _conn()
    _ensure_vp_missions_table(conn)
    mission_id = "vp-mission-coder-dead01"
    _insert_task(
        conn,
        task_id=mission_id,
        source_kind="vp_mission",
        metadata_json=_vp_mirror_meta(mission_id, agent_id="vp.coder.primary"),
    )
    expired_lease = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    _insert_vp_mission(
        conn,
        mission_id=mission_id,
        status="running",
        claim_expires_at=expired_lease,
        vp_id="vp.coder.primary",
    )

    task_hub.reconcile_task_lifecycle(
        conn,
        running_session_ids=set(),
        rebuild_queue=False,
        cron_live_grace_seconds=0,
    )

    item = task_hub.get_item(conn, mission_id)
    assert item["status"] == task_hub.TASK_STATUS_OPEN, (
        "an expired-lease Cody mission must be reaped/reopened"
    )
    assert _disposition_reason(conn, mission_id) == "reconciled_orphaned_in_progress"
