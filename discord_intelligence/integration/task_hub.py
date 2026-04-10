from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
import uuid
import logging
from ..config import init_secrets

logger = logging.getLogger(__name__)

def create_task_hub_mission(title: str, description: str, tags: list = None):
    init_secrets()
    conn = None
    try:
        conn = connect_runtime_db(get_activity_db_path())
        task_hub.ensure_schema(conn)
        task_id = str(uuid.uuid4())
        task_data = {
            "task_id": task_id,
            "title": title,
            "description": description,
            "status": getattr(task_hub, 'TASK_STATUS_OPEN', 'open'),
            "project_key": "immediate",
            "agent_ready": 1,
            "metadata": {
                "source": "discord_intelligence", 
                "tags": tags or []
            }
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
