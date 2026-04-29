"""Shared helpers for creating proactive Task Hub items.

Consolidates the repeated ``task_hub.upsert_item()`` call structure used by
proactive_codie, proactive_tutorial_builds, and proactive_convergence.
"""

from __future__ import annotations

from typing import Any
import sqlite3

from universal_agent import task_hub


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
) -> dict[str, Any]:
    """Create a standardized proactive task in Task Hub.

    Centralises the common fields shared by all proactive services:
    ``project_key="proactive"``, ``status=open``, ``agent_ready=True``,
    ``trigger_type="heartbeat_poll"``, and priority clamping to [1, 4].
    """
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
            "agent_ready": True,
            "trigger_type": "heartbeat_poll",
            "metadata": metadata or {},
        },
    )
    return item
