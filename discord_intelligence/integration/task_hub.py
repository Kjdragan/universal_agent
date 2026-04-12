"""Task Hub integration for Discord intelligence.

Provides both direct-DB functions (for task creation) and gateway-backed
async functions (for approval/rejection actions that need concurrency safety).
"""

import logging
import uuid
from typing import Any

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

from ..config import init_secrets
from . import gateway_client

logger = logging.getLogger(__name__)


def create_task_hub_mission(
    title: str,
    description: str,
    tags: list[str] | None = None,
    *,
    agent_ready: bool = True,
    source_kind: str = "discord_intelligence",
    metadata: dict[str, Any] | None = None,
):
    init_secrets()
    conn = None
    try:
        conn = connect_runtime_db(get_activity_db_path())
        task_hub.ensure_schema(conn)
        task_id = str(uuid.uuid4())
        labels = tags or []
        task_metadata = {
            "source": source_kind,
            "tags": labels,
            **(metadata or {}),
        }
        task_data = {
            "task_id": task_id,
            "title": title,
            "description": description,
            "status": getattr(task_hub, 'TASK_STATUS_OPEN', 'open'),
            "project_key": "immediate",
            "source_kind": source_kind,
            "agent_ready": bool(agent_ready),
            "labels": labels,
            "metadata": task_metadata,
        }
        task_hub.upsert_item(conn, task_data)
        logger.info(f"Created Task Hub mission: {task_id}")
        return task_id
    except Exception as e:
        logger.error(f"Failed to create Task Hub item: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_task_hub_items(status: str = None, limit: int = 10):
    init_secrets()
    conn = None
    try:
        conn = connect_runtime_db(get_activity_db_path())
        task_hub.ensure_schema(conn)
        
        query = "SELECT task_id, title, status, priority FROM task_hub_items"
        args = []
        if status:
            query += " WHERE status = ?"
            args.append(status)
        
        query += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        
        cur = conn.execute(query, tuple(args))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to fetch Task Hub items: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_mission_status(task_id: str):
    init_secrets()
    conn = None
    try:
        conn = connect_runtime_db(get_activity_db_path())
        task_hub.ensure_schema(conn)

        row = conn.execute("SELECT * FROM task_hub_items WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return dict(row)
    except Exception as e:
        logger.error(f"Failed to fetch mission {task_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Gateway-backed async functions (for approval/rejection actions)
# ---------------------------------------------------------------------------

async def approve_task_via_gateway(
    task_id: str, agent_id: str = "discord_operator"
) -> dict[str, Any]:
    """Approve a task through the gateway REST API."""
    return await gateway_client.approve_task(task_id)


async def reject_task_via_gateway(
    task_id: str, reason: str = "", agent_id: str = "discord_operator"
) -> dict[str, Any]:
    """Reject (park) a task through the gateway REST API."""
    return await gateway_client.task_action(
        task_id, "park", reason=reason, agent_id=agent_id,
    )


async def get_review_tasks_via_gateway() -> list[dict[str, Any]]:
    """Fetch tasks needing human review through the gateway REST API."""
    return await gateway_client.get_review_tasks()
