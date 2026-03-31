from __future__ import annotations

import sqlite3
import os
from datetime import datetime, timedelta, timezone

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def test_upsert_open_refresh_does_not_clobber_in_progress_state() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "csi:test-incident",
                "source_kind": "csi",
                "source_ref": "evt-1",
                "title": "CSI Incident",
                "description": "Initial dispatch",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_IN_PROGRESS,
                "must_complete": True,
                "agent_ready": True,
                "incident_key": "test-incident",
                "seizure_state": "seized",
            },
        )

        refreshed = task_hub.upsert_csi_item(
            conn,
            event_id="evt-2",
            event_type="csi_update",
            source="csi_analytics",
            title="CSI Incident",
            message="Latest update payload",
            project_key="immediate",
            labels=["agent-ready", "must-complete"],
            priority=4,
            incident_key="test-incident",
            must_complete=True,
            mirror_status="internal",
            routing_state=task_hub.CSI_ROUTING_AGENT_ACTIONABLE,
        )

        assert refreshed["status"] == task_hub.TASK_STATUS_IN_PROGRESS
        assert refreshed["seizure_state"] == "seized"
    finally:
        conn.close()


def test_finalize_assignments_reopens_in_progress_items_in_legacy_mode() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:dispatch-1",
                "source_kind": "internal",
                "title": "Dispatch Candidate",
                "description": "Needs handling",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:s1")
        assert len(claimed) == 1
        assignment_id = str(claimed[0]["assignment_id"])

        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="heartbeat_run_finished",
            reopen_in_progress=True,
        )

        assert result["finalized"] == 1
        assert result["reopened"] == 1
        assert result["reviewed"] == 0
        assert result["retry_exhausted"] == 0
        item = task_hub.get_item(conn, "task:dispatch-1")
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_OPEN
        assert item["seizure_state"] == "unseized"
    finally:
        conn.close()


def test_finalize_assignments_heartbeat_success_moves_unresolved_to_review() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:heartbeat-success",
                "source_kind": "internal",
                "title": "Heartbeat candidate",
                "description": "Needs explicit disposition",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:sx")
        assignment_id = str(claimed[0]["assignment_id"])

        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="heartbeat_run_finished",
            reopen_in_progress=True,
            policy="heartbeat",
            heartbeat_max_retries=3,
        )

        assert result["finalized"] == 1
        assert result["reviewed"] == 1
        assert result["reopened"] == 0
        item = task_hub.get_item(conn, "task:heartbeat-success")
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_REVIEW
    finally:
        conn.close()


def test_finalize_assignments_heartbeat_failure_retries_then_exhausts_to_review() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:heartbeat-failure",
                "source_kind": "internal",
                "title": "Retry candidate",
                "description": "Should retry then review",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )

        first_claim = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:r1")
        first_assignment = str(first_claim[0]["assignment_id"])
        first = task_hub.finalize_assignments(
            conn,
            assignment_ids=[first_assignment],
            state="failed",
            result_summary="heartbeat_failed",
            reopen_in_progress=True,
            policy="heartbeat",
            heartbeat_max_retries=2,
        )
        assert first["reopened"] == 1
        assert first["retry_exhausted"] == 0
        reopened_item = task_hub.get_item(conn, "task:heartbeat-failure")
        assert reopened_item is not None
        assert reopened_item["status"] == task_hub.TASK_STATUS_OPEN

        second_claim = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:r2")
        second_assignment = str(second_claim[0]["assignment_id"])
        second = task_hub.finalize_assignments(
            conn,
            assignment_ids=[second_assignment],
            state="failed",
            result_summary="heartbeat_failed_again",
            reopen_in_progress=True,
            policy="heartbeat",
            heartbeat_max_retries=2,
        )
        assert second["reopened"] == 0
        assert second["reviewed"] == 1
        assert second["retry_exhausted"] == 1
        exhausted_item = task_hub.get_item(conn, "task:heartbeat-failure")
        assert exhausted_item is not None
        assert exhausted_item["status"] == task_hub.TASK_STATUS_REVIEW
    finally:
        conn.close()


def test_finalize_assignments_heartbeat_keeps_explicitly_completed_items_completed() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:heartbeat-explicit-complete",
                "source_kind": "internal",
                "title": "Complete candidate",
                "description": "Will be completed explicitly",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )
        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:complete")
        assignment_id = str(claimed[0]["assignment_id"])
        task_hub.perform_task_action(
            conn,
            task_id="task:heartbeat-explicit-complete",
            action="complete",
            reason="done",
            agent_id="heartbeat:complete",
        )

        result = task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="heartbeat_run_finished",
            reopen_in_progress=True,
            policy="heartbeat",
            heartbeat_max_retries=3,
        )

        assert result["completed"] == 1
        assert result["reopened"] == 0
        assert result["reviewed"] == 0
        item = task_hub.get_item(conn, "task:heartbeat-explicit-complete")
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_COMPLETED
    finally:
        conn.close()


def test_finalize_assignments_todo_failure_retries_then_exhausts_to_review(monkeypatch) -> None:
    conn = _conn()
    monkeypatch.setenv("UA_TASK_HUB_TODO_MAX_RETRIES", "2")
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:todo-failure",
                "source_kind": "email",
                "title": "Work item retry candidate",
                "description": "Should retry once then move to review",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )

        first_claim = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="todo:daemon_simone_todo")
        first_assignment = str(first_claim[0]["assignment_id"])
        first = task_hub.finalize_assignments(
            conn,
            assignment_ids=[first_assignment],
            state="failed",
            result_summary="todo_failed",
            reopen_in_progress=True,
            policy="todo",
        )
        assert first["reopened"] == 1
        assert first["retry_exhausted"] == 0
        reopened_item = task_hub.get_item(conn, "task:todo-failure")
        assert reopened_item is not None
        assert reopened_item["status"] == task_hub.TASK_STATUS_OPEN
        assert reopened_item["metadata"]["dispatch"]["todo_retry_count"] == 1

        second_claim = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="todo:daemon_simone_todo")
        second_assignment = str(second_claim[0]["assignment_id"])
        second = task_hub.finalize_assignments(
            conn,
            assignment_ids=[second_assignment],
            state="failed",
            result_summary="todo_failed_again",
            reopen_in_progress=True,
            policy="todo",
        )
        assert second["reopened"] == 0
        assert second["reviewed"] == 1
        assert second["retry_exhausted"] == 1
        exhausted_item = task_hub.get_item(conn, "task:todo-failure")
        assert exhausted_item is not None
        assert exhausted_item["status"] == task_hub.TASK_STATUS_REVIEW
        assert exhausted_item["metadata"]["dispatch"]["last_disposition_reason"] == "todo_retry_exhausted"
    finally:
        monkeypatch.delenv("UA_TASK_HUB_TODO_MAX_RETRIES", raising=False)
        conn.close()


def test_release_stale_assignments_abandons_old_heartbeat_claims() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:stale-1",
                "source_kind": "internal",
                "title": "Stale dispatch",
                "description": "Old heartbeat claim",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:s2")
        assert len(claimed) == 1
        assignment_id = str(claimed[0]["assignment_id"])

        old_started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute(
            "UPDATE task_hub_assignments SET started_at=? WHERE assignment_id=?",
            (old_started, assignment_id),
        )
        conn.commit()

        result = task_hub.release_stale_assignments(
            conn,
            agent_id_prefix="heartbeat:",
            stale_after_seconds=300,
        )

        assert result["stale_detected"] == 1
        assert result["finalized"] == 1
        assert result["reopened"] == 1
        row = conn.execute(
            "SELECT state, ended_at FROM task_hub_assignments WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        assert row is not None
        assert str(row["state"]) == "abandoned"
        assert str(row["ended_at"] or "").strip() != ""
    finally:
        conn.close()


def test_park_csi_items_not_matching_event_types() -> None:
    conn = _conn()
    try:
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-keep",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Keep me",
            message="opportunity event",
            project_key="mission",
            labels=["CSI", "agent-ready"],
            priority=3,
            incident_key="incident-keep",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_AGENT_ACTIONABLE,
        )
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-park",
            event_type="hourly_token_usage_report",
            source="csi_analytics",
            title="Park me",
            message="routine event",
            project_key="csi",
            labels=["CSI", "agent-ready"],
            priority=2,
            incident_key="incident-park",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_AGENT_ACTIONABLE,
        )

        result = task_hub.park_csi_items_not_matching_event_types(
            conn,
            allowed_event_types={"opportunity_bundle_ready"},
            park_reason="unit_test_policy",
        )

        assert result["parked"] == 1
        keep = task_hub.get_item(conn, "csi:incident-keep")
        park = task_hub.get_item(conn, "csi:incident-park")
        assert keep is not None and keep["status"] == task_hub.TASK_STATUS_OPEN
        assert park is not None and park["status"] == task_hub.TASK_STATUS_PARKED
        assert str((park.get("metadata") or {}).get("auto_parked_reason")) == "unit_test_policy"
    finally:
        conn.close()


def test_list_agent_queue_collapses_time_suffixed_opportunity_incident_keys() -> None:
    conn = _conn()
    try:
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-bundle-1",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Bundle test 1",
            message="bundle event 1",
            project_key="csi",
            labels=["CSI", "agent-ready"],
            priority=3,
            incident_key="opportunity_bundle:test:2026030101",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_AGENT_ACTIONABLE,
        )
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-bundle-2",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Bundle test 2",
            message="bundle event 2",
            project_key="csi",
            labels=["CSI", "agent-ready"],
            priority=3,
            incident_key="opportunity_bundle:test:2026030102",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_AGENT_ACTIONABLE,
        )
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-bundle-3",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Bundle other",
            message="bundle event 3",
            project_key="csi",
            labels=["CSI", "agent-ready"],
            priority=3,
            incident_key="opportunity_bundle:other:2026030103",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_AGENT_ACTIONABLE,
        )

        queue = task_hub.list_agent_queue(conn, include_csi=True, collapse_csi=True, limit=50)
        csi_rows = [row for row in (queue.get("items") or []) if str(row.get("source_kind") or "") == "csi"]
        collapsed_counts = sorted(int(row.get("collapsed_count") or 1) for row in csi_rows)

        assert len(csi_rows) == 2
        assert collapsed_counts == [1, 2]
        normalized_values = {str(row.get("incident_key_normalized") or "") for row in csi_rows}
        assert "opportunity_bundle:test" in normalized_values
    finally:
        conn.close()


def test_overview_counts_normalized_csi_incidents() -> None:
    conn = _conn()
    try:
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-overview-1",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Bundle A",
            message="overview bundle a",
            project_key="csi",
            labels=["CSI", "agent-ready"],
            priority=3,
            incident_key="opportunity_bundle:topic-a:202603011200",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_INCUBATING,
        )
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-overview-2",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Bundle A refresh",
            message="overview bundle a refresh",
            project_key="csi",
            labels=["CSI", "agent-ready"],
            priority=3,
            incident_key="opportunity_bundle:topic-a:202603011230",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_INCUBATING,
        )
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-overview-3",
            event_type="delivery_reliability_slo_breached",
            source="csi_analytics",
            title="SLO breached",
            message="hard failure",
            project_key="immediate",
            labels=["CSI", "agent-ready", "must-complete"],
            priority=4,
            incident_key="delivery_reliability_slo_breached:csi_analytics",
            must_complete=True,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED,
            human_intervention_reason="SLO breach requires operator review.",
        )

        summary = task_hub.overview(conn)
        csi_summary = summary.get("csi_incident_summary") if isinstance(summary.get("csi_incident_summary"), dict) else {}
        queue_health = summary.get("queue_health") if isinstance(summary.get("queue_health"), dict) else {}
        assert int(csi_summary.get("open_incidents") or 0) == 2
        assert int(queue_health.get("threshold") or 0) >= 1
        assert int(queue_health.get("csi_incubating_hidden") or 0) == 2
        assert int(queue_health.get("csi_human_open") or 0) == 1
        assert int(queue_health.get("csi_agent_actionable_open") or 0) == 0
    finally:
        conn.close()


def test_csi_incubating_items_are_hidden_from_agent_and_personal_queues() -> None:
    conn = _conn()
    try:
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-incubating",
            event_type="opportunity_bundle_ready",
            source="csi_analytics",
            title="Incubating opportunity",
            message="still maturing",
            project_key="mission",
            labels=["CSI"],
            priority=3,
            incident_key="opportunity_bundle:hidden:202603011200",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_INCUBATING,
            routing_reason="csi_owned_maturation_in_progress",
        )

        agent_queue = task_hub.list_agent_queue(conn, include_csi=True, collapse_csi=False, limit=50)
        personal_queue = task_hub.list_personal_queue(conn, limit=50)

        assert not [row for row in (agent_queue.get("items") or []) if str(row.get("source_kind") or "") == "csi"]
        assert not [row for row in personal_queue if str(row.get("source_kind") or "") == "csi"]
    finally:
        conn.close()


def test_csi_human_intervention_items_surface_in_personal_queue() -> None:
    conn = _conn()
    try:
        task_hub.upsert_csi_item(
            conn,
            event_id="evt-human",
            event_type="csi_global_brief_review_due",
            source="csi_analytics",
            title="Review latest CSI brief",
            message="requires review",
            project_key="csi",
            labels=["CSI", "needs-human"],
            priority=3,
            incident_key="global_trend_brief:topic-a",
            must_complete=False,
            mirror_status="internal_only",
            routing_state=task_hub.CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED,
            routing_reason="scheduled_brief_review_due",
            human_intervention_reason="Review latest CSI global trend brief.",
        )

        personal_queue = task_hub.list_personal_queue(conn, limit=50)
        csi_rows = [row for row in personal_queue if str(row.get("source_kind") or "") == "csi"]

        assert len(csi_rows) == 1
        assert str(csi_rows[0].get("csi_routing_state") or "") == task_hub.CSI_ROUTING_HUMAN_INTERVENTION_REQUIRED
        assert str((((csi_rows[0].get("metadata") or {}).get("csi") or {}).get("human_intervention_reason") or "")) == (
            "Review latest CSI global trend brief."
        )
    finally:
        conn.close()


def test_system_schedule_review_task_is_dispatch_eligible() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "scmd:schedule-heartbeat",
                "source_kind": "system_command",
                "source_ref": "ops",
                "title": "Change heartbeat schedule",
                "description": "Command block instruction",
                "project_key": "immediate",
                "priority": 2,
                "labels": ["agent-ready", "schedule-command"],
                "status": task_hub.TASK_STATUS_REVIEW,
                "must_complete": False,
                "agent_ready": True,
                "metadata": {
                    "intent": "schedule_task",
                    "schedule_text": "every ten minutes",
                    "repeat_schedule": True,
                },
            },
        )
        task_hub.rebuild_dispatch_queue(conn)
        queue = task_hub.get_dispatch_queue(conn, limit=20)
        rows = [row for row in (queue.get("items") or []) if row.get("task_id") == "scmd:schedule-heartbeat"]
        assert len(rows) == 1
        assert rows[0]["eligible"] is True
        assert rows[0]["skip_reason"] is None
    finally:
        conn.close()


def test_dispatch_queue_low_priority_task_is_eligible_at_default_threshold() -> None:
    """With the default threshold of 3, even a priority-1 agent-ready task (base
    score 4.2) is eligible for dispatch — thresholds influence ORDER, not ELIGIBILITY."""
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:low-pri-eligible",
                "source_kind": "internal",
                "source_ref": "ops",
                "title": "Low priority task",
                "description": "Should be eligible at default threshold",
                "project_key": "immediate",
                "priority": 1,
                "labels": ["agent-ready"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": False,
                "agent_ready": True,
            },
        )
        task_hub.rebuild_dispatch_queue(conn)
        queue = task_hub.get_dispatch_queue(conn, limit=20)
        rows = [row for row in (queue.get("items") or []) if row.get("task_id") == "task:low-pri-eligible"]
        assert len(rows) == 1
        assert rows[0]["eligible"] is True
        assert rows[0]["skip_reason"] is None
    finally:
        conn.close()


def test_system_schedule_task_ranks_ahead_of_must_complete_backlog() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:must-complete-csi",
                "source_kind": "csi",
                "source_ref": "evt-100",
                "title": "CSI must complete",
                "description": "Background incident",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "scmd:urgent-schedule",
                "source_kind": "system_command",
                "source_ref": "ops",
                "title": "Adjust heartbeat cadence",
                "description": "Operator command",
                "project_key": "immediate",
                "priority": 2,
                "labels": ["agent-ready", "schedule-command"],
                "status": task_hub.TASK_STATUS_REVIEW,
                "must_complete": False,
                "agent_ready": True,
                "metadata": {
                    "intent": "schedule_task",
                    "schedule_text": "every ten minutes",
                    "repeat_schedule": True,
                },
            },
        )
        queue = task_hub.get_dispatch_queue(conn, limit=10)
        items = queue.get("items") or []
        assert len(items) >= 2
        assert items[0]["task_id"] == "scmd:urgent-schedule"
    finally:
        conn.close()


def test_claim_next_dispatch_tasks_skips_ineligible_front_rows() -> None:
    conn = _conn()
    previous_threshold = os.environ.get("UA_TASK_HUB_AGENT_THRESHOLD")
    os.environ["UA_TASK_HUB_AGENT_THRESHOLD"] = "1"
    try:
        for idx in range(7):
            task_hub.upsert_item(
                conn,
                {
                    "task_id": f"task:review-front-{idx}",
                    "source_kind": "internal",
                    "source_ref": "ops",
                    "title": f"Front review {idx}",
                    "description": "Non-dispatchable review item",
                    "project_key": "immediate",
                    "priority": 4,
                    "labels": ["agent-ready", "must-complete"],
                    "status": task_hub.TASK_STATUS_REVIEW,
                    "must_complete": True,
                    "agent_ready": True,
                },
            )
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:eligible-open",
                "source_kind": "internal",
                "source_ref": "ops",
                "title": "Eligible open task",
                "description": "Should still be claimable",
                "project_key": "immediate",
                "priority": 1,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )

        queue = task_hub.get_dispatch_queue(conn, limit=8)
        assert queue["items"][0]["task_id"].startswith("task:review-front-")
        assert queue["items"][0]["eligible"] is False

        claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id="heartbeat:claim-scan")
        assert len(claimed) == 1
        assert claimed[0]["task_id"] == "task:eligible-open"
        assert claimed[0]["status"] == task_hub.TASK_STATUS_IN_PROGRESS
    finally:
        if previous_threshold is None:
            os.environ.pop("UA_TASK_HUB_AGENT_THRESHOLD", None)
        else:
            os.environ["UA_TASK_HUB_AGENT_THRESHOLD"] = previous_threshold
        conn.close()


def test_completed_list_and_task_history_include_session_links() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:completed-history",
                "source_kind": "internal",
                "source_ref": "ops",
                "title": "Completed history task",
                "description": "Track this in history",
                "project_key": "immediate",
                "priority": 4,
                "labels": ["agent-ready", "must-complete"],
                "status": task_hub.TASK_STATUS_OPEN,
                "must_complete": True,
                "agent_ready": True,
            },
        )
        claimed = task_hub.claim_next_dispatch_tasks(
            conn,
            limit=1,
            agent_id="heartbeat:sess-history",
            workflow_run_id="run-heartbeat-1",
            workflow_attempt_id="attempt-heartbeat-1",
            provider_session_id="sess-history",
        )
        assert len(claimed) == 1
        assignment_id = str(claimed[0]["assignment_id"])
        task_hub.perform_task_action(
            conn,
            task_id="task:completed-history",
            action="complete",
            reason="done",
            agent_id="heartbeat:sess-history",
        )
        task_hub.finalize_assignments(
            conn,
            assignment_ids=[assignment_id],
            state="completed",
            result_summary="completed",
            reopen_in_progress=False,
            policy="heartbeat",
        )

        completed = task_hub.list_completed_tasks(conn, limit=20)
        target = next((row for row in completed if row.get("task_id") == "task:completed-history"), None)
        assert target is not None
        assignment = target.get("last_assignment") if isinstance(target.get("last_assignment"), dict) else {}
        assert assignment.get("session_id") == "sess-history"
        assert assignment.get("workflow_run_id") == "run-heartbeat-1"
        assert assignment.get("workflow_attempt_id") == "attempt-heartbeat-1"
        assert assignment.get("provider_session_id") == "sess-history"

        history = task_hub.get_task_history(conn, task_id="task:completed-history", limit=20)
        assignments = history.get("assignments") if isinstance(history.get("assignments"), list) else []
        assert len(assignments) >= 1
        assert assignments[0]["session_id"] == "sess-history"
        assert assignments[0]["workflow_run_id"] == "run-heartbeat-1"
        assert assignments[0]["workflow_attempt_id"] == "attempt-heartbeat-1"
        assert assignments[0]["provider_session_id"] == "sess-history"
    finally:
        conn.close()
