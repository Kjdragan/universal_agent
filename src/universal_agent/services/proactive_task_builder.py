"""Shared helpers for creating proactive Task Hub items.

Consolidates the repeated ``task_hub.upsert_item()`` call structure used by
proactive_codie, proactive_tutorial_builds, and proactive_convergence.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.feature_flags import task_hub_missions_enabled

logger = logging.getLogger(__name__)


def create_meaningful_work_mission(
    conn: sqlite3.Connection,
    *,
    root_task_id: str,
    title: str,
    description: str,
    source_kind: str,
    source_ref: str,
    project_key: str,
    priority: int,
    labels: list[str] | None = None,
    phases: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a mission envelope plus the first executable child task.

    Returns a dict with:
      - ``mission``: parent mission-envelope task
      - ``task``: first spawned child phase task (if any), otherwise the mission
    """
    if not task_hub_missions_enabled():
        raise ValueError("task hub missions are disabled")
    safe_task_id = str(root_task_id or "").strip()
    if not safe_task_id:
        raise ValueError("root_task_id is required")
    normalized_phases = list(phases or [])
    mission = task_hub.create_mission_envelope(
        conn,
        task_id=safe_task_id,
        title=title,
        description=description,
        source_kind=source_kind,
        source_ref=source_ref,
        project_key=project_key,
        priority=priority,
        labels=labels or ["mission-envelope"],
        mission_plan={
            "mission_title": title,
            "phases": normalized_phases,
        },
        metadata=metadata or {},
    )
    children = task_hub.list_workstream_tasks(conn, safe_task_id, include_parent=False)
    first_child = children[0] if children else mission
    return {"mission": mission, "task": first_child}


def queue_proactive_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    source_kind: str,
    source_ref: str,
    title: str,
    description: str,
    priority: int = 2,
    labels: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    agent_ready: bool = True,
) -> dict[str, Any]:
    """Create a standardized proactive task in Task Hub.

    Centralises the common fields shared by all proactive services:
    ``project_key="proactive"``, ``status=open``, ``agent_ready=True``,
    ``trigger_type="heartbeat_poll"``, and priority clamping to [1, 4].

    ``agent_ready`` defaults to True (the historical behavior). Callers that
    want to queue a *pending-approval* row — visible/open but not yet
    dispatch-eligible — pass ``agent_ready=False`` AND drop the ``agent-ready``
    label, otherwise ``task_hub.upsert_item`` re-derives ``agent_ready=True``
    from the label (the OR-fallback at ``upsert_item``).

    Before creating the task, checks the preference model.  If *all*
    matching preference dimensions carry strongly-negative weight the
    task is silently suppressed to avoid wasting compute on work the
    user has explicitly indicated disinterest in.
    """
    # ── Preference gate (hard block) ──────────────────────────────
    try:
        from universal_agent.services.proactive_preferences import (
            should_block_proactive_task,
        )

        # Derive topic tags from labels for preference matching
        topic_tags = [
            label for label in (labels or [])
            if label not in {"agent-ready"}
        ]
        blocked, reason = should_block_proactive_task(
            conn,
            task_type=source_kind,
            topic_tags=topic_tags,
        )
        if blocked:
            logger.info(
                "Proactive task suppressed by preference gate: task_id=%s reason=%s",
                task_id, reason,
            )
            return {
                "task_id": task_id,
                "status": "preference_blocked",
                "title": title,
                "blocked_reason": reason,
            }
    except Exception as exc:
        # Fail-open: preference system errors must not block task creation
        logger.debug("Preference gate check failed (allowing task): %s", exc)

    clamped = max(1, min(int(priority or 2), 4))
    item = task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "title": title,
            "description": description,
            "project_key": "proactive",
            "priority": clamped,
            "labels": labels or ["agent-ready"],
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": bool(agent_ready),
            "trigger_type": "heartbeat_poll",
            "metadata": metadata or {},
        },
    )
    return item
