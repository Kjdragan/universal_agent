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
            "task_content": f"{title}\n\n{description}",
            "status": getattr(task_hub, 'TASK_STATUS_OPEN', 'open'),
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
