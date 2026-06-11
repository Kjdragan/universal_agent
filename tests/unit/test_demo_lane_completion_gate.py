"""Demo-lane completion-evidence gate (2026-06-11 incident).

Simone's rescue-evaluator LLM — dispatched the ``vp_failure`` item for a
mission demoted to failed (``missing_completion_attestation``) — called
``task_hub_task_action(action="complete")`` on the SOURCE ``tutorial_build``
task (``tutorial-build:f08d721d27eaaea4``), producing a completed row with
empty ``demo_finalize`` and bypassing the entire P6 deterministic finalize.

The gate in ``task_hub.py::perform_task_action`` (complete branch): a
non-operator ``complete`` on a ``DEMO_LANE_COMPLETION_GATED_SOURCE_KINDS``
task is honored only when ``metadata.vp_terminal_status == "completed"``
AND ``metadata.demo_finalize.ok`` is truthy; otherwise the task routes to
``needs_review`` with ``completion_blocked_reason=completion_requires_demo_finalize``.
Operator surfaces (``dashboard_operator`` / ``operator*``) bypass the gate.
"""

from __future__ import annotations

import sqlite3

from universal_agent import task_hub


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _seed_demo_task(
    conn: sqlite3.Connection,
    *,
    task_id: str = "tutorial-build:gatetest1",
    source_kind: str = "tutorial_build",
    metadata: dict | None = None,
) -> str:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "source_ref": "gatetest-video",
            "title": "Build private tutorial repo: gate test",
            "description": "gate test",
            "status": "needs_review",
            "metadata": metadata or {},
        },
    )
    return task_id


def test_llm_complete_without_finalize_evidence_routes_to_review():
    """The incident shape: linked mission failed, no demo_finalize —
    a non-operator complete must NOT complete the task."""
    conn = _conn()
    task_id = _seed_demo_task(
        conn, metadata={"vp_terminal_status": "failed"}
    )

    result = task_hub.perform_task_action(
        conn,
        task_id=task_id,
        action="complete",
        note="Demo built; missing attestation is a protocol formality.",
        agent_id="simone",
    )

    assert result["status"] == task_hub.TASK_STATUS_REVIEW
    dispatch = dict(result["metadata"].get("dispatch") or {})
    assert dispatch.get("completion_blocked_reason") == "completion_requires_demo_finalize"
    assert dispatch.get("completion_unverified") is True


def test_llm_complete_with_full_finalize_evidence_is_honored():
    """When the linked mission completed AND finalize evidence exists, a
    non-operator complete is a harmless re-complete — allowed."""
    conn = _conn()
    task_id = _seed_demo_task(
        conn,
        metadata={
            "vp_terminal_status": "completed",
            "demo_finalize": {"ok": True, "demo_id": "gate-demo"},
            # Explicit non-email channel so the email-delivery gate
            # doesn't interfere with what this test pins.
            "workflow_manifest": {"final_channel": "chat"},
        },
    )

    result = task_hub.perform_task_action(
        conn, task_id=task_id, action="complete", agent_id="simone"
    )

    assert result["status"] == task_hub.TASK_STATUS_COMPLETED


def test_operator_complete_bypasses_the_gate():
    """dashboard_operator keeps full manual override even with zero
    finalize evidence (the operator is the human appeal path)."""
    conn = _conn()
    task_id = _seed_demo_task(
        conn,
        metadata={
            "vp_terminal_status": "failed",
            "workflow_manifest": {"final_channel": "chat"},
        },
    )

    result = task_hub.perform_task_action(
        conn, task_id=task_id, action="complete", agent_id="dashboard_operator"
    )

    assert result["status"] == task_hub.TASK_STATUS_COMPLETED


def test_non_demo_lanes_unaffected():
    """The gate is scoped to demo lanes; other source_kinds keep the
    existing complete semantics."""
    conn = _conn()
    task_id = _seed_demo_task(
        conn,
        task_id="qa-gatetest",
        source_kind="qa",
        metadata={"workflow_manifest": {"final_channel": "chat"}},
    )

    result = task_hub.perform_task_action(
        conn, task_id=task_id, action="complete", agent_id="simone"
    )

    assert result["status"] == task_hub.TASK_STATUS_COMPLETED
