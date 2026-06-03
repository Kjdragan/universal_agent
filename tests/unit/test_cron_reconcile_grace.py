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
